#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.

""" Module defining the Charmed operator for the FINOS Legend Engine Server. """

import json
import logging

from ops import charm
from ops import framework
from ops import main
from ops import model


logger = logging.getLogger(__name__)

ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH = "/engine-config.json"

APPLICATION_CONNECTOR_TYPE_HTTP = "http"
APPLICATION_CONNECTOR_TYPE_HTTPS = "https"

VALID_APPLICATION_LOG_LEVEL_SETTINGS = [
    "INFO", "WARN", "DEBUG", "TRACE", "OFF"]

GITLAB_PROJECT_VISIBILITY_PUBLIC = "public"
GITLAB_PROJECT_VISIBILITY_PRIVATE = "private"
GITLAB_REQUIRED_SCOPES = ["openid", "profile", "api"]
GITLAB_OPENID_DISCOVERY_URL = (
    "https://gitlab.com/.well-known/openid-configuration")


class LegendEngineServerOperatorCharm(charm.CharmBase):
    """ Charmed operator for the FINOS Legend Engine Server. """

    _stored = framework.StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self._set_stored_defaults()

        # Standard charm lifecycle events:
        self.framework.observe(
            self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.engine_pebble_ready, self._on_engine_pebble_ready)

        # DB relation lifecycle events:
        self.framework.observe(
            self.on["legend-db"].relation_joined,
            self._on_db_relation_joined)
        self.framework.observe(
            self.on["legend-db"].relation_changed,
            self._on_db_relation_changed)

    def _set_stored_defaults(self) -> None:
        self._stored.set_default(log_level="DEBUG")
        self._stored.set_default(mongodb_credentials={})

    def _on_engine_pebble_ready(self, event: framework.EventBase) -> None:
        """Define the Engine workload using the Pebble API.
        Note that this will *not* start the service, but instead leave it in a
        blocked state until the relevant relations required for it are added.
        """
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload

        # Define an initial Pebble layer configuration
        pebble_layer = {
            "summary": "Engine layer.",
            "description": "Pebble config layer for FINOS Legend Engine Server.",
            "services": {
                "engine": {
                    "override": "replace",
                    "summary": "engine",
                    "command": (
                        # NOTE(aznashwan): starting through bash is required
                        # for the classpath glob (-cp ...) to be expanded:
                        "/bin/sh -c 'java -XX:+ExitOnOutOfMemoryError -Xss4M "
                        "-XX:MaxRAMPercentage=60 -Dfile.encoding=UTF8 "
                        "-cp /app/bin/*-shaded.jar org.finos.legend.engine."
                        "server.Server server %s'" % (
                            ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH
                        )
                    ),
                    # NOTE(aznashwan): considering the Engine service expects
                    # a singular config file which already contains all
                    # relevant options in it (some of which will require the
                    # relation with Mongo/Gitlab to have already been
                    # established), we do not auto-start:
                    "startup": "disabled",
                    # TODO(aznashwan): determine any env vars we could pass
                    # (most notably, things like the RAM percentage etc...)
                    "environment": {},
                }
            },
        }

        # Add intial Pebble config layer using the Pebble API
        container.add_layer("engine", pebble_layer, combine=True)

        # NOTE(aznashwan): as mentioned above, we will *not* be auto-starting
        # the service until the relations with Mongo and Gitlab are added:
        # container.autostart()

        self.unit.status = model.BlockedStatus(
            "Awaiting Legend SDLC, Mongo, and Gitlab relations.")

    def _get_logging_level_from_config(self, option_name) -> str:
        """Fetches the config option with the given name and checks to
        ensure that it is a valid `java.utils.logging` log level.

        Returns None if an option is invalid.
        """
        value = self.model.config[option_name]
        if value not in VALID_APPLICATION_LOG_LEVEL_SETTINGS:
            logger.warning(
                "Invalid Java logging level value provided for option "
                "'%s': '%s'. Valid Java logging levels are: %s. The charm "
                "shall block until a proper value is set.",
                option_name, value, VALID_APPLICATION_LOG_LEVEL_SETTINGS)
            return None
        return value

    def _add_base_service_config_from_charm_config(
            self, engine_config: dict = {}) -> model.BlockedStatus:
        """This method adds all relevant engine config options into the
        provided dict to be directly rendered as JSON and passed to
        the engine service during startup.

        Returns:
            None if all of the config options derived from the config/relations
            are present and have passed Charm-side valiation steps.
            A `model.BlockedStatus` instance with a relevant message otherwise.
        """
        # Check gitlab-related options:
        # TODO(aznashwan): remove this check on eventual Gitlab relation:
        gitlab_client_id = self.model.config.get('gitlab-client-id')
        gitlab_client_secret = self.model.config.get('gitlab-client-secret')
        if not all([gitlab_client_id, gitlab_client_secret]):
            return model.BlockedStatus(
                "One or more Gitlab-related charm configuration options "
                "are missing.")

        # Check Java logging options:
        pac4j_logging_level = self._get_logging_level_from_config(
            "server-pac4j-logging-level")
        server_logging_level = self._get_logging_level_from_config(
            "server-logging-level")
        server_logging_format = self.model.config['server-logging-format']
        if not all([pac4j_logging_level, pac4j_logging_level]):
            return model.BlockedStatus(
                "One or more logging config options are improperly formatted "
                "or missing. Please review the debug-log for more details.")

        # Check Mongo-related options:
        mongo_creds = self._stored.mongodb_credentials
        if not mongo_creds or 'replica_set_uri' not in mongo_creds:
            return model.BlockedStatus(
                "No stored MongoDB credentials were found yet. Please "
                "ensure the Charm is properly related to MongoDB.")
        mongo_replica_set_uri = self._stored.mongodb_credentials[
            'replica_set_uri']
        databases = mongo_creds.get('databases')
        database_name = None
        if databases:
            database_name = databases[0]
            # NOTE(aznashwan): the Java MongoDB can't handle DB names in the
            # URL, so we need to trim that part and pass the database name
            # as a separate parameter within the config as the
            # engine_config['pac4j']['mongoDb'] option below.
            split_uri = [
                elem
                for elem in mongo_replica_set_uri.split('/')[:-1]
                # NOTE: filter any empty strings resulting from double-slashes:
                if elem]
            # NOTE: schema prefix needs two slashes added back:
            mongo_replica_set_uri = "%s//%s" % (
                split_uri[0], "/".join(split_uri[1:]))

        # Compile base config:
        engine_config.update({
            "deployment": {
                "mode": self.model.config['server-deployment-mode']
            },
            "logging": {
                "level": server_logging_level,
                "loggers": {
                    "root": {
                        "level": server_logging_level,
                    },
                    "org.pac4j": {
                        "level": pac4j_logging_level
                    }
                },
                "appenders": [{
                    "type": "console",
                    "logFormat": server_logging_format
                }]
            },
            "pac4j": {
                "callbackPrefix": "",
                "mongoUri": mongo_replica_set_uri,
                "mongoDb": database_name,
                "bypassPaths": ["/api/server/v1/info"],
                "clients": [{
                    "org.finos.legend.server.pac4j.gitlab.GitlabClient": {
                        "name": "gitlab",
                        "clientId": gitlab_client_id,
                        "secret": gitlab_client_secret,
                        "discoveryUri": GITLAB_OPENID_DISCOVERY_URL,
                        # NOTE(aznashwan): needs to be a space-separated str:
                        "scope": " ".join(GITLAB_REQUIRED_SCOPES)
                    }
                }],
                "mongoSession": {
                    "enabled": True,
                    "collection": "userSessions"
                }
            },
            # TODO(aznashwan): ask whether these options are
            # relevant and/or worth exposing:
            "opentracing": {
                "elastic": "",
                "zipkin": "",
                "uri": "",
                "authenticator": {
                    "principal": "",
                    "keytab": ""
                }
            },
            "swagger": {
                "title": "Legend Engine",
                "resourcePackage": "org.finos.legend",
                "uriPrefix": self.model.config['server-root-path']
            },
            "server": {
                "type": "simple",
                "applicationContextPath": "/",
                "adminContextPath": "/admin",
                "requestLog": {"appenders": []},
                "connector": {
                    "maxRequestHeaderSize": "32KiB",
                    "type": APPLICATION_CONNECTOR_TYPE_HTTP,
                    "port": self.model.config[
                        'server-application-connector-port-http']
                },
            },
            # TODO(aznashwan): check whether this is how you reference the SDLC
            "metadataserver": {
                "pure": {
                    "host": "127.0.0.1",
                    "port": 8090
                }
            },
            "vaults": []
        })

        return None

    def _update_engine_service_config(
            self, container: model.Container, config: dict) -> None:
        """Renders provided config to JSON and pushes it to the container
        through the Pebble files API.
        """
        logger.debug(
            "Adding following config under '%s' in container: %s",
            ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH, config)
        container.push(
            ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH,
            json.dumps(config),
            make_dirs=True)
        logger.info(
            "Successfully wrote config file '%s'",
            ENGINE_CONFIG_FILE_CONTAINER_LOCAL_PATH)

    def _restart_engine_service(self, container: model.Container) -> None:
        """Restarts the Engine service using the Pebble container API.
        """
        logger.debug("Restarting Engine service")
        container.restart("engine")
        logger.debug("Successfully issues Engine service restart")

    def _reconfigure_engine_service(self) -> None:
        """Generates the JSON config for the Engine server and adds it into
        the container via Pebble files API.
        - regenerating the JSON config for the Engine server
        - adding it via Pebble
        - instructing Pebble to restart the Engine server
        The Service is power-cycled for the new configuration to take effect.
        """
        config = {}
        possible_blocked_status = (
            self._add_base_service_config_from_charm_config(config))
        if possible_blocked_status:
            logger.warning("Missing/erroneous configuration options")
            self.unit.status = possible_blocked_status
            return

        container = self.unit.get_container("engine")
        if container.can_connect():
            logger.debug("Updating Engine service configuration")
            self._update_engine_service_config(container, config)
            self._restart_engine_service(container)
            self.unit.status = model.ActiveStatus(
                "Engine service has been started.")
            return

        logger.info("Engine container is not active yet. No config to update.")
        self.unit.status = model.BlockedStatus(
            "Awaiting Legend SDLC, Mongo, and Gitlab relations.")

    def _on_config_changed(self, _) -> None:
        """Reacts to configuration changes to the service by:
        - regenerating the JSON config for the Engine service
        - adding it via Pebble
        - instructing Pebble to restart the Engine service
        """
        self._reconfigure_engine_service()

    def _on_db_relation_joined(self, event: charm.RelationJoinedEvent):
        logger.debug("No actions are to be performed during Mongo relation join")

    def _on_db_relation_changed(
            self, event: charm.RelationChangedEvent) -> None:
        rel_id = event.relation.id
        rel = self.framework.model.get_relation("legend-db", rel_id)
        mongo_creds_json = rel.data[event.app].get("legend-db-connection")
        if not mongo_creds_json:
            self.unit.status = model.WaitingStatus(
                "Awaiting DB relation data.")
            event.defer()
            return
        logger.debug(
            "Mongo JSON credentials returned by DB relation: %s",
            mongo_creds_json)

        mongo_creds = None
        try:
            mongo_creds = json.loads(mongo_creds_json)
        except (ValueError, TypeError) as ex:
            logger.warn(
                "Exception occured while deserializing DB relation "
                "connection data: %s", str(ex))
            self.unit.status = model.BlockedStatus(
                "Could not deserialize Legend DB connection data.")
            return
        logger.debug(
            "Deserialized Mongo credentials returned by DB relation: %s",
            mongo_creds)

        self._stored.mongodb_credentials = mongo_creds

        # Attempt to reconfigure and restart the service with the new data:
        self._reconfigure_engine_service()


if __name__ == "__main__":
    main.main(LegendEngineServerOperatorCharm)
