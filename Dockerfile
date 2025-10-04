###########################################
#         Base Python Image              #
###########################################
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/te/build/inst/default/bin:$PATH"

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  gettext \
  python3-celery \
  gosu \
  util-linux \
  rsync \
  flex \
  bison \
  ninja-build \
  libjansson-dev \
  libjansson-doc \
  libjansson4 \
  libpopt-dev \
  libpcre3-dev \
  pixz \
  libxml-parser-perl \
  build-essential \
  curl \
  libkrb5-dev \
  libffi-dev \
  libxml2-dev \
  libyaml-dev \
  libssl-dev \
  libglib2.0-dev \
  git \
  && rm -rf /var/lib/apt/lists/* \
  && cpan -T JSON

# Install UV
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN chmod +x /uv-installer.sh && /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"
ENV UV_HTTP_TIMEOUT=2400

# Install dependencies using uv pip
RUN uv pip install --system --no-cache-dir meson==1.6.1 watchfiles==1.0.4

RUN mkdir bublik

COPY ./bublik/requirements.txt /app/bublik/requirements.txt
RUN uv pip install --system --no-cache-dir -r /app/bublik/requirements.txt

COPY ./entrypoint-common.sh /app/bublik/entrypoint-common.sh
COPY ./entrypoint-django.sh /app/bublik/entrypoint-django.sh
COPY ./entrypoint-celery.sh /app/bublik/entrypoint-celery.sh
COPY ./entrypoint-logserver.sh /app/bublik/entrypoint-logserver.sh
RUN chmod +x /app/bublik/entrypoint-*.sh

WORKDIR /app/te
COPY ./test-environment .
RUN ./dispatcher.sh -q --conf-builder=builder.conf.tools --no-run

###########################################
#         Documentation 
###########################################
FROM node:22.13-alpine AS docs-base

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN npm i -g corepack@latest
RUN corepack enable

WORKDIR /app

COPY ./bublik-release/package.json ./bublik-release/pnpm-lock.yaml ./

RUN pnpm config set registry https://registry.npmjs.org
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

COPY ./bublik-release .

FROM docs-base AS docs-builder

ARG URL_PREFIX
ARG DOCS_URL=http://localhost

WORKDIR /app

RUN URL="${DOCS_URL}" BASE_URL="${URL_PREFIX}/docs/" pnpm run build

###########################################
#           Bublik Runner               #
###########################################
FROM base AS runner

WORKDIR /app

COPY --from=docs-builder /app/build /app/bublik/docs

COPY ./bublik ./bublik

RUN mkdir -p ./bublik/logs

WORKDIR /app/bublik

###########################################
#           Log Server                    #
###########################################
FROM base AS log-server

RUN apt-get update && apt-get install -y \
    apache2 \
    file \
    jq \
    inotify-tools \
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod cgid

RUN mkdir -p \
  /home/te-logs/cgi-bin \
  /home/te-logs/logs \
  /home/te-logs/incoming \
  /home/te-logs/bad \
  /home/te-logs/bin \
  /app/bublik \
  /app/te-templates \
  && chmod -R 775 /home/te-logs/logs \
  && chmod -R 775 /home/te-logs/incoming \
  && chmod -R 775 /home/te-logs/bad

COPY ./entrypoint-common.sh /app/bublik/entrypoint-common.sh
COPY ./entrypoint-logserver.sh /app/bublik/entrypoint-logserver.sh
RUN chmod +x /app/bublik/entrypoint-*.sh

COPY ./test-environment/tools/log_server/te-logs-error404.template /app/te-templates/
COPY ./test-environment/tools/log_server/te-logs-index.template /app/te-templates/
COPY ./test-environment/tools/log_server/publish-logs-unpack.sh /app/te-templates/
COPY ./test-environment/tools/log_server/publish-incoming-logs.template /app/te-templates/
COPY ./test-environment/tools/log_server/apache2-te-log-server.conf.template /app/te-templates/

RUN echo "ServerName localhost" >> /etc/apache2/apache2.conf

RUN ln -sf /proc/self/fd/1 /var/log/apache2/access.log && \
  ln -sf /proc/self/fd/2 /var/log/apache2/error.log

RUN sed -i \
  -e 's|ErrorLog ${APACHE_LOG_DIR}/error.log|ErrorLog /proc/self/fd/2|' \
  -e 's|CustomLog ${APACHE_LOG_DIR}/access.log combined|CustomLog /proc/self/fd/1 combined|' \
  /etc/apache2/apache2.conf

RUN mkdir -p /app/te-logs-static && \
  cd /app/te/build/inst/default/share/rgt-format/xml2html-multi && \
  cp -r /app/te/build/inst/default/share/rgt-format/xml2html-multi/images /app/te-logs-static/ && \
  find . -type f -not -path "./images/*" -exec cp {} /app/te-logs-static/ \; && \
  chmod -R 755 /app/te-logs-static

EXPOSE ${BUBLIK_DOCKER_TE_LOG_SERVER_PORT}
ENTRYPOINT ["/app/bublik/entrypoint-logserver.sh"]
