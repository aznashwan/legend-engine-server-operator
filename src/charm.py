#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.

""" Module defining the Charmed operator for the FINOS Legend Engine. """

import json
import logging

from charms.finos_legend_libs.v0 import legend_operator_base
from ops import charm, main, model

logger = logging.getLogger(__name__)

ENGINE_SERVICE_NAME = "engine"
ENGINE_CONTAINER_NAME = "engine"
LEGEND_DB_RELATION_NAME = "legend-db"
LEGEND_GITLAB_RELATION_NAME = "legend-engine-gitlab"
LEGEND_STUDIO_RELATION_NAME = "legend-engine"

ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH = "/engine-config.json"
ENGINE_SERVICE_URL_FORMAT = "%(schema)s://%(host)s:%(port)s%(path)s"
ENGINE_GITLAB_REDIRECT_URI_FORMAT = "%(base_url)s/callback"

TRUSTSTORE_PASSPHRASE = "Legend Engine"
TRUSTSTORE_CONTAINER_LOCAL_PATH = "/truststore.jks"

APPLICATION_CONNECTOR_PORT_HTTP = 6060
APPLICATION_CONNECTOR_PORT_HTTPS = 6066
APPLICATION_ROOT_PATH = "/api"

APPLICATION_LOGGING_FORMAT = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5p [%thread] %c - %m%n"

GITLAB_REQUIRED_SCOPES = ["openid", "profile", "api"]


class LegendEngineServerCharm(legend_operator_base.BaseFinosLegendCoreServiceCharm):
    """Charmed operator for the FINOS Legend Engine Server."""

    def __init__(self, *args):
        super().__init__(*args)

        # Studio relation events:
        self.framework.observe(
            self.on[LEGEND_STUDIO_RELATION_NAME].relation_joined, self._on_studio_relation_joined
        )
        self.framework.observe(
            self.on[LEGEND_STUDIO_RELATION_NAME].relation_changed, self._on_studio_relation_changed
        )

    @classmethod
    def _get_application_connector_port(cls):
        return APPLICATION_CONNECTOR_PORT_HTTP

    @classmethod
    def _get_workload_container_name(cls):
        return ENGINE_CONTAINER_NAME

    @classmethod
    def _get_workload_service_names(cls):
        return [ENGINE_SERVICE_NAME]

    @classmethod
    def _get_workload_pebble_layers(cls):
        return {
            "engine": {
                "summary": "Engine layer.",
                "description": "Pebble config layer for FINOS Legend Engine.",
                "services": {
                    "engine": {
                        "override": "replace",
                        "summary": "engine",
                        "command": (
                            # NOTE(aznashwan): starting through bash is needed
                            # for the classpath glob (-cp ...) to be expanded:
                            "/bin/sh -c 'java -XX:+ExitOnOutOfMemoryError "
                            "-Xss4M -XX:MaxRAMPercentage=60 "
                            "-Dfile.encoding=UTF8 "
                            '-Djavax.net.ssl.trustStore="%s" '
                            '-Djavax.net.ssl.trustStorePassword="%s" '
                            "-cp /app/bin/*-shaded.jar org.finos.legend."
                            "engine.server.Server server %s'"
                            % (
                                TRUSTSTORE_CONTAINER_LOCAL_PATH,
                                TRUSTSTORE_PASSPHRASE,
                                ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH,
                            )
                        ),
                        # NOTE(aznashwan): considering the Engine service
                        # expects a singular config file which already contains
                        # all relevant options in it (some of which will
                        # require the relation with Mongo/GitLab to have
                        # already been established), we do not auto-start:
                        "startup": "disabled",
                        # TODO(aznashwan): determine any env vars we could pass
                        # (most notably, things like the RAM percentage etc...)
                        "environment": {},
                    }
                },
            }
        }

    def _get_jks_truststore_preferences(self):
        jks_prefs = {
            "truststore_path": TRUSTSTORE_CONTAINER_LOCAL_PATH,
            "truststore_passphrase": TRUSTSTORE_PASSPHRASE,
            "trusted_certificates": {},
        }
        cert = self._get_legend_gitlab_certificate()
        if cert:
            # NOTE(aznashwan): cert label 'gitlab-engine' is arbitrary:
            jks_prefs["trusted_certificates"]["gitlab-engine"] = cert
        return jks_prefs

    @classmethod
    def _get_legend_gitlab_relation_name(cls):
        return LEGEND_GITLAB_RELATION_NAME

    @classmethod
    def _get_legend_db_relation_name(cls):
        return LEGEND_DB_RELATION_NAME

    def _get_engine_service_url(self):
        ip_address = legend_operator_base.get_ip_address()
        return ENGINE_SERVICE_URL_FORMAT % (
            {
                # NOTE(aznashwan): we always return the plain HTTP endpoint:
                "schema": legend_operator_base.APPLICATION_CONNECTOR_TYPE_HTTP,
                "host": ip_address,
                "port": APPLICATION_CONNECTOR_PORT_HTTP,
                "path": APPLICATION_ROOT_PATH,
            }
        )

    def _get_legend_gitlab_redirect_uris(self):
        base_url = self._get_engine_service_url()
        redirect_uris = [ENGINE_GITLAB_REDIRECT_URI_FORMAT % {"base_url": base_url}]
        return redirect_uris

    def _get_core_legend_service_configs(self, legend_db_credentials, legend_gitlab_credentials):
        # Check Mongo-related options:
        if not legend_db_credentials:
            return model.WaitingStatus("no legend db info present in relation yet")

        # Check gitlab-related options:
        if not legend_gitlab_credentials:
            return model.WaitingStatus("no legend gitlab info present in relation yet")
        gitlab_client_id = legend_gitlab_credentials["client_id"]
        gitlab_client_secret = legend_gitlab_credentials["client_secret"]
        gitlab_openid_discovery_url = legend_gitlab_credentials["openid_discovery_url"]

        # Check Java logging options:
        pac4j_logging_level = self._get_logging_level_from_config("server-pac4j-logging-level")
        server_logging_level = self._get_logging_level_from_config("server-logging-level")
        if not all([pac4j_logging_level, pac4j_logging_level]):
            return model.BlockedStatus(
                "one or more logging config options are improperly formatted "
                "or missing, please review the debug-log for more details"
            )

        # Compile base config:
        engine_config = {
            "deployment": {"mode": self.model.config["server-deployment-mode"]},
            "logging": {
                "level": server_logging_level,
                "loggers": {
                    "root": {
                        "level": server_logging_level,
                    },
                    "org.pac4j": {"level": pac4j_logging_level},
                },
                "appenders": [{"type": "console", "logFormat": APPLICATION_LOGGING_FORMAT}],
            },
            "pac4j": {
                "callbackPrefix": "",
                "mongoUri": legend_db_credentials["uri"],
                "mongoDb": legend_db_credentials["database"],
                "bypassPaths": ["/api/server/v1/info"],
                "clients": [
                    {
                        "org.finos.legend.server.pac4j.gitlab.GitlabClient": {
                            "name": "gitlab",
                            "clientId": gitlab_client_id,
                            "secret": gitlab_client_secret,
                            "discoveryUri": gitlab_openid_discovery_url,
                            # NOTE(aznashwan): needs to be a space-separated str:
                            "scope": " ".join(GITLAB_REQUIRED_SCOPES),
                        }
                    }
                ],
                "mongoSession": {"enabled": True, "collection": "userSessions"},
            },
            # TODO(aznashwan): ask whether these options are
            # relevant and/or worth exposing:
            "opentracing": {
                "elastic": "",
                "zipkin": "",
                "uri": "",
                "authenticator": {"principal": "", "keytab": ""},
            },
            "swagger": {
                "title": "Legend Engine",
                "resourcePackage": "org.finos.legend",
                "uriPrefix": APPLICATION_ROOT_PATH,
            },
            "server": {
                "type": "simple",
                "applicationContextPath": "/",
                "adminContextPath": "/admin",
                "requestLog": {"appenders": []},
                "connector": {
                    "maxRequestHeaderSize": "32KiB",
                    "type": legend_operator_base.APPLICATION_CONNECTOR_TYPE_HTTP,
                    "port": APPLICATION_CONNECTOR_PORT_HTTP,
                },
            },
            "metadataserver": {"pure": {"host": "127.0.0.1", "port": 8090}},
            "vaults": [],
        }

        return {ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH: (json.dumps(engine_config, indent=4))}

    def _on_studio_relation_joined(self, event: charm.RelationJoinedEvent) -> None:
        rel = event.relation
        engine_url = self._get_engine_service_url()
        logger.info("Providing following Engine URL to Studio: %s", engine_url)
        rel.data[self.app]["legend-engine-url"] = engine_url

    def _on_studio_relation_changed(self, event: charm.RelationChangedEvent) -> None:
        pass


if __name__ == "__main__":
    main.main(LegendEngineServerCharm)
