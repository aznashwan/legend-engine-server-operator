# Copyright 2021 Canonical
# See LICENSE file for licensing details.

"""Module defining the Base FINOS Legend Charmed operator classes and utilities."""

import abc
import base64
import logging
import subprocess
import traceback

import jks
from ops import charm
from ops import framework
from ops import model
from OpenSSL import crypto

from charms.finos_legend_db_k8s.v0 import legend_database
from charms.finos_legend_gitlab_integrator_k8s.v0 import legend_gitlab
from charms.nginx_ingress_integrator.v0 import ingress


# The unique Charmhub library identifier, never change it
LIBID = "43dd472db93a416eae84a8206e96c2ce"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

logger = logging.getLogger(__name__)

APPLICATION_CONNECTOR_TYPE_HTTP = "http"
APPLICATION_CONNECTOR_TYPE_HTTPS = "https"
GITLAB_REQUIRED_SCOPES = ["openid", "profile", "api"]

VALID_APPLICATION_LOG_LEVEL_SETTINGS = [
    "INFO", "WARN", "DEBUG", "TRACE", "OFF"]

TRUSTSTORE_TYPE_JKS = "jks"


def add_file_to_container(
        container, file_path, file_data, make_dirs=True,
        raise_on_error=True):
    """Adds a file with the given data under the given filepath via Pebble API.

    Args:
        container: `ops.model.Container` instance to add the file into.
        file_path: string absolute path to the file to write.
        file_data: string data to write into the file.
        make_dirs: whether or not to create parent directories if needed.
        raise_on_error: whether or not the function should re-raise errors.

    Returns:
        True if the file was successfully added, False (or raises) otherwise.

    Raises:
        ops.prebble.ProtocolError: if the container connection fails.
        ops.pebble.PathError: if the path is invalid and/or make_dirs=False
    """
    logger.debug(
        "Adding file '%s' in container '%s'", file_path, container.name)
    try:
        container.push(file_path, file_data, make_dirs=make_dirs)
    except Exception:
        logger.error(
            "Exception occurred while adding file '%s' in container '%s':\n%s",
            file_path, container.name, traceback.format_exc())
        if raise_on_error:
            raise
        return False
    logger.debug(
        "Successfully added file '%s' in container '%s'",
        file_path, container.name)
    return True


def parse_base64_certificate(b64_cert):
    """Parses the provided base64-encoded X509 certificate and returns the
    afferent `OpenSSL.crypto.X509` instance for it.

    Args:
        b64_cert: str or bytes representation of the base64-encoded cert.

    Returns:
        `OpenSSL.crypto.X509` instance.

    Raises:
        ValueError: on input/base64 formatting issues.
        OpenSSL.crypto.Error: on any OpenSSL-related operation failing.
    """
    if not isinstance(b64_cert, (str, bytes)):
        raise ValueError(
            "Argument must be either str or bytes. Got: %s" % b64_cert)

    raw_cert = base64.b64decode(b64_cert)
    formats = [crypto.FILETYPE_PEM, crypto.FILETYPE_ASN1]
    format_errors = {}
    certificate = None
    for fmt in formats:
        try:
            certificate = crypto.load_certificate(fmt, raw_cert)
            break
        except Exception:
            format_errors[fmt] = traceback.format_exc()
    if not certificate:
        raise ValueError(
            "Failed to load certificate. Per-format errors were: %s" % (
                format_errors))
    return certificate


def create_jks_truststore_with_certificates(certificates):
    """Creates a `jks.KeyStore` with the provided certificates.

    Args:
        certificates: dict of the form:
        {
            "cert1": <OpenSSL.crypto.X509>,
            "cert2": <OpenSSL.crypto.X509>
        }

    Returns:
        `jks.KeyStore` with the provided certificates added as trusted.

    Raises:
        ValueError: if provided anything but a list of `OpenSSL.crypto.X509`s.
    """
    if not isinstance(certificates, dict):
        raise ValueError("Requires dict of strings, got: %s")
    if not all([
            isinstance(c, crypto.X509) for c in certificates.values()]):
        raise ValueError(
            "Requires a dictionary of strings to `OpenSSL.crypto.X509` "
            "instances. Got: %r" % certificates)

    cert_entries = []
    for cert_name, cert in certificates.items():
        dumped_cert = crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)
        entry = jks.TrustedCertEntry.new(cert_name, dumped_cert)
        cert_entries.append(entry)
    return jks.KeyStore.new(
        TRUSTSTORE_TYPE_JKS, cert_entries)


def get_ip_address():
    """Simply runs a `unit-get private-address` to return the IP address.

    Returns:
        String IP address.

    Raises:
        `subprocess.CalledProcessError`: if there are any errors running the process.
    """
    ip_address = subprocess.check_output(
        ["unit-get", "private-address"])
    return ip_address.decode().strip()


class BaseFinosLegendCharm(charm.CharmBase):
    """Base class which abstracts base functionality shared amongst FINOS
    Legend Charmed operators with the aim of minimising implementation
    duplication.

    The core assumptions made by this class on the Charm's workload are:
    * the workload will consist of a simple set of Legend Services
    * said Legend service requires certain relations/configs to be written
      within the container for the service to be started
    * the service cannot (and should not) be left running if any one of the
      required relations for it are not present

    Key methods to override would be:
    * _get_workload_pebble_layers: return Pebble layers for the service(s)
    * _get_required_relations: list of relations needed by the Service
    * _get_service_configs: config files for the Service
    * _get_jks_truststore_preferences: JKS truststore settings (if needed)
    """

    _stored = framework.StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self._set_stored_defaults()

        # TODO(aznashwan): worth always setting up an ingress for all services?
        self.ingress = ingress.IngressRequires(
            self, {
                "service-hostname": self.app.name,
                "service-name": self.app.name,
                "service-port": self._get_application_connector_port()})

        # Standard charm lifecycle events:
        self.framework.observe(
            self.on.config_changed, self._on_config_changed)
        container_ready_attr = "%s_pebble_ready" % (
            self._workload_container_name)
        self.framework.observe(
            getattr(self.on, container_ready_attr),
            self._on_workload_container_pebble_ready)

    def _set_stored_defaults(self):
        pass

    @classmethod
    @abc.abstractmethod
    def _get_required_relations(cls):
        """Returns a list of relation names which should be considered
        mandatory for the Legend service to be started.

        Returns:
            list(str) of relation names denoting mandatory relations.
        """
        raise NotImplementedError("No required relations list defined.")

    @classmethod
    @abc.abstractmethod
    def _get_application_connector_port(cls):
        """Returns the integer port number the application is listening on."""
        raise NotImplementedError("No application connector port defined.")

    @classmethod
    @abc.abstractmethod
    def _get_workload_container_name(cls):
        """Returns the string name of the main workload container."""
        raise NotImplementedError("No workload container name defined.")

    @classmethod
    @abc.abstractmethod
    def _get_workload_service_names(cls):
        """Returns a list of service names within the workload container."""
        raise NotImplementedError("No wokload service names defined.")

    @abc.abstractmethod
    def _get_workload_pebble_layers(self):
        """Returns a dict mapping service labels to their Pebble layer."""
        raise NotImplementedError("No workload Pebble layers defined.")

    @abc.abstractmethod
    def _get_jks_truststore_preferences(self):
        """Returns preferences on how the JKS truststore should be created
        within the workload container.
        Can return `None` to indicate no JKS truststore setup is required.

        Returns: None or dict of the form:
        {
            "truststore_path": "/container/path/to/put/truststore.jks",
            "truststore_passphrase": "<passphrase for truststore>",
            "trusted_certificates": {
                "cert_name_1": "<base64-encoded DER or PEM>",
                "cert_name_2": "<base64-encoded DER or PEM>"
            }
        }
        """
        raise NotImplementedError("No truststore preferences defined.")

    @abc.abstractmethod
    def _get_service_configs(self, relations_data):
        """Returns a list of config files required for the Legend service.

        Args:
            relations_data: dict between relation name and their afferent
                relation data. It is guaranteed that the relations are
                established by the time this method will be called, but no
                guarantees on the required relation data already being there
                can be made. The charm should return a `model.WaitingStatus`
                if additional relation data is needed.

        Returns:
            A `model.BlockedStatus` on error.
            A `model.WaitingStatus` if any relation data is missing.
            If all relations are present, a dict of the following form:
            {
                "/path/to/config-file-1.txt": "<contents of config file>",
                "/legend.json": '{"example": "config"}'
            }
        """
        raise NotImplementedError("No Legend service config implemented.")

    def _get_logging_level_from_config(self, option_name) -> str:
        """Fetches the config option with the given name and checks to
        ensure that it is a valid `java.utils.logging` log level.

        Args:
            option_name: string name of the config option.

        Returns:
            String logging level to be passed to the Legend service.

        Returns None if an option is invalid.
        """
        value = self.model.config[option_name]
        if value not in VALID_APPLICATION_LOG_LEVEL_SETTINGS:
            logger.warning(
                "Invalid Java logging level value provided for option "
                "'%s': '%s'. Valid Java logging levels are: %s. The charm "
                "shall block until a proper value is set.",
                option_name, value,
                VALID_APPLICATION_LOG_LEVEL_SETTINGS)
            return None
        return value

    @property
    def _workload_container_name(self):
        """Returns the string name of the main workload container."""
        return self._get_workload_container_name()

    @property
    def _workload_container(self):
        """Returns the `model.Container` pertaining to this unit.

        Returns:
            A `model.Container` instance or None if container isn't up yet.
        """
        container = self.unit.get_container(self._workload_container_name)
        if container.can_connect():
            return container
        return None

    def _update_charm_status(self, status: model.StatusBase):
        """Sets the provided status for this unit as well as the application
        itself. (given the unit is the leader) """
        self.unit.status = status
        if self.unit.is_leader():
            self.app.status = status

    def _update_status_and_services(
            self, container: model.Container, status: model.StatusBase):
        """Updates the status of the charm (and parent app if the unit is the
        leader) and performs the following action on the Legend service(s):
        * `model.ActiveStatus`: (re)starts the service(s)
        * `model.WaitingStatus`: no action taken
        * `model.BlockedStatus`: stops the service(s)
        """
        try:
            if isinstance(status, model.ActiveStatus):
                self._restart_legend_services(container)
            elif isinstance(status, model.WaitingStatus):
                logger.debug("No action needed on WaitingStatus")
            else:
                self._stop_legend_services(container)
        except Exception:
            logger.error(
                "Exception occurred while updating Legend services as part of "
                "handling status '%s'. Error was: %s",
                status, traceback.format_exc())
            status = model.BlockedStatus(
                "failed to apply Legend services changes")
        self._update_charm_status(status)

    def _get_relation(
            self, relation_name, relation_id=None,
            raise_on_multiple_relations=True):
        """Returns the `model.Relation` with the given name.

        Args:
            relation_name: string name of the relation.
            relation_id: optional ID of the relation.


        Returns:
            A `model.Relation` instance with the given name, or None.

        Raises:
            model.TooManyRelatedAppsError: if multiple relation are found and
                                           no specific relation_id is given.
        """
        relation = None
        try:
            relation = self.framework.model.get_relation(
                relation_name, relation_id)
        except model.TooManyRelatedAppsError:
            logger.error(
                "Too many relations of type '%s'. Error was: %s",
                relation_name, traceback.format_exc())
            if raise_on_multiple_relations:
                raise
        return relation

    def _setup_jks_truststore(self, container, truststore_preferences):
        """Sets up the JKS truststore in the provided container with the
        properties returned by `_get_jks_truststore_preferences()`.

        Args:
            container: the `model.Container` instance to add the truststore in.
            truststore_preferences: dict of the form:
            {
                "truststore_path": "/container/path/to/put/truststore.jks",
                "truststore_passphrase": "<passphrase for truststore>",
                "trusted_certificates": {
                    "cert_name_1": "<base64-encoded DER or PEM>",
                    "cert_name_2": "<base64-encoded DER or PEM>"
                }
            }

        Returns:
            A `model.BlockedStatus` on any issue, else None.
        """
        # Check provided truststore params:
        required_truststore_prefs_keys = [
            "truststore_path", "truststore_passphrase", "trusted_certificates"]
        if not isinstance(truststore_preferences, dict):
            return model.BlockedStatus(
                "invalid JKS truststore preferences: %s" % (
                    truststore_preferences))
        if not all([truststore_preferences.get(k) is not None
                    for k in required_truststore_prefs_keys]):
            return model.BlockedStatus(
                "invalid JKS truststore preferences: %s" % (
                    truststore_preferences))
        certs = truststore_preferences['trusted_certificates']
        if not isinstance(certs, dict):
            return model.BlockedStatus(
                "invalid JKS truststore certs list given")

        # Parse all the certs:
        trusted_certs = {}
        for cert_name, cert_b64 in truststore_preferences["trusted_certificates"].items():
            try:
                trusted_certs[cert_name] = parse_base64_certificate(cert_b64)
            except Exception:
                logger.error(
                    "Exception occurred while parsing cert '%s': %s",
                    cert_name, traceback.format_exc())
                return model.BlockedStatus(
                    "failed to parse JKS truststore cert '%s'" % cert_name)

        # Create truststore:
        truststore = None
        try:
            truststore = create_jks_truststore_with_certificates(trusted_certs)
        except Exception:
            logger.error(
                "Exception occurred while creating truststore with params "
                "%s: %s", truststore_preferences, traceback.format_exc())
            return model.BlockedStatus("failed to create jks truststore")

        # Add truststore to container;
        dumped_truststore = truststore.saves(
            truststore_preferences['truststore_passphrase'])
        if not add_file_to_container(
                container, truststore_preferences['truststore_path'],
                dumped_truststore, raise_on_error=False):
            return model.BlockedStatus(
                "failed to write jks truststore to container")

    def _restart_legend_services(self, container):
        """Restarts the workload(s) within the container."""
        services = self._get_workload_service_names()
        logger.debug(
            "Restarting Legend services from container '%s': %s",
            container.name, services)
        container.restart(*services)

    def _stop_legend_services(self, container):
        """Stops the workload(s) within the container."""
        services = self._get_workload_service_names()
        logger.debug(
            "Stopping Legend services from container '%s': %s",
            container.name, services)
        container.stop(*services)

    def _refresh_charm_status(self):
        """Refreshes the Legend charm status by:
        * checking if the Pebble API in the workload container is reachable
        * checking all relations present
        * setting up JKS truststore in container (if the service needs it)
        * composing service config(s) using `_get_service_configs()`
        * writing the config file(s) to the container
        """
        # Check workload container ready:
        container = self._workload_container
        if not container or not container.can_connect():
            self._update_status_and_services(
                container, model.WaitingStatus("awaiting workload container"))
            return

        # Check all required relations present:
        required_relations = {
            rel: None for rel in self._get_required_relations()}
        try:
            required_relations = {
                rel: self._get_relation(rel)
                for rel in required_relations}
        except Exception as ex:
            logger.error(
                "Exception occurred while fetching relations %s: %s",
                list(required_relations), traceback.format_exc())
            self._update_status_and_services(
                container, model.BlockedStatus(
                    "error reading relations: %s" % str(ex)))
            return
        missing_relations = [
            rel for rel, res in required_relations.items() if not res]
        if missing_relations:
            self._update_status_and_services(
                container, model.BlockedStatus(
                    "missing following relations: %s" % (
                        ", ".join(missing_relations))))
            return

        # Setup JKS truststore:
        try:
            truststore_preferences = self._get_jks_truststore_preferences()
        except Exception:
            logger.error(
                "Exception occurred while querying JKS truststore "
                "preferences: %s", traceback.format_exc())
            self._update_status_and_services(
                container, model.BlockedStatus(
                    "error querying JKS truststore preferences"))
            return
        if truststore_preferences:
            possible_blocked_status = self._setup_jks_truststore(
                container, truststore_preferences)
            if possible_blocked_status:
                self._update_status_and_services(
                    container, possible_blocked_status)
                return
        else:
            logger.debug(
                "Legend Charm '%s' indicated no JKS truststore setup "
                "was required for it.", self.app.name)

        # Setup service configs:
        relations_data_map = {
            rel: relv.data[relv.app]
            for rel, relv in required_relations.items()}
        configs = None
        try:
            configs = self._get_service_configs(relations_data_map)
        except Exception:
            logger.error(
                "Exception occurred while composing service configs: %s",
                traceback.format_exc())
            self._update_status_and_services(
                container,
                model.BlockedStatus("error composing service configs"))
            return
        if isinstance(configs, (model.BlockedStatus, model.WaitingStatus)):
            self._update_status_and_services(container, configs)
            return
        for conf_path, conf_data in configs.items():
            try:
                add_file_to_container(
                    container, conf_path, conf_data, make_dirs=True)
            except Exception:
                # NOTE(aznashwan): `add_file_to_container` already
                # logs the traceback so no point duplicating it here.
                self._update_status_and_services(
                    container, model.BlockedStatus(
                        "error adding config file '%s' to workload "
                        "container" % conf_path))
                return

        # Start the now-reconfigured workloads:
        self._update_status_and_services(container, model.ActiveStatus())

    def _on_config_changed(self, event: charm.ConfigChangedEvent):
        """Refreshes the service config."""
        self._refresh_charm_status()

    def _on_workload_container_pebble_ready(
            self, event: charm.PebbleReadyEvent):
        """Adds the Pebble layers to the container upon its availability."""
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload

        # Add the base Pebble layer:
        layers = self._get_workload_pebble_layers()
        for label, layer in layers.items():
            container.add_layer(label, layer, combine=True)

        # NOTE(aznashwan): we will *not* be auto-starting the service
        # to allow for the `_refresh_charm_status` method do determine
        # if all the relations are present and write the configs itself:
        # container.autostart()

        self._refresh_charm_status()


class BaseFinosLegendCoreServiceCharm(BaseFinosLegendCharm):
    """Base class which abstracts base functionality shared amongst FINOS
    Legend Charmed core service operators.

    The term "core service" is used to describe FINOS Legend services which:
    * the workload will consist of a simple set of Legend Services
    * said Legend service require the presence of GitLab and MongoDB relations
    * the service cannot (and should not) be left running if any one of the
      required relations for it are not present
    """

    def __init__(self, *args):
        super().__init__(*args)

        self._set_stored_defaults()

        # Get relation names:
        legend_db_relation_name = self._get_legend_db_relation_name()
        legend_gitlab_relation_name = self._get_legend_gitlab_relation_name()

        # Related charm library consumers:
        self._legend_db_consumer = legend_database.LegendDatabaseConsumer(
            self, relation_name=legend_db_relation_name)
        self._legend_gitlab_consumer = legend_gitlab.LegendGitlabConsumer(
            self, relation_name=legend_gitlab_relation_name)

        # DB relation lifecycle events:
        self.framework.observe(
            self.on[legend_db_relation_name].relation_joined,
            self._on_db_relation_joined)
        self.framework.observe(
            self.on[legend_db_relation_name].relation_changed,
            self._on_db_relation_changed)
        self.framework.observe(
            self.on[legend_db_relation_name].relation_broken,
            self._on_db_relation_broken)

        # GitLab integrator relation lifecycle events:
        self.framework.observe(
            self.on[legend_gitlab_relation_name].relation_joined,
            self._on_legend_gitlab_relation_joined)
        self.framework.observe(
            self.on[legend_gitlab_relation_name].relation_changed,
            self._on_legend_gitlab_relation_changed)
        self.framework.observe(
            self.on[legend_gitlab_relation_name].relation_broken,
            self._on_legend_gitlab_relation_broken)

    @classmethod
    @abc.abstractmethod
    def _get_legend_gitlab_relation_name(cls):
        """Returns the string name of the GitLab Integrator relation."""
        raise NotImplementedError("No GitLab relation name defined.")

    @abc.abstractmethod
    def _get_legend_gitlab_redirect_uris(self):
        """Returns a list of strings of redirect URIs to be set within the
        GitLab application upon its creation."""
        raise NotImplementedError("No GitLab redirect URIs defined.")

    @classmethod
    @abc.abstractmethod
    def _get_legend_db_relation_name(cls):
        """Returns the string name of the Legend DB Manager relation."""
        raise NotImplementedError("No Legend DB relation name defined.")

    @classmethod
    def _get_required_relations(cls):
        """Returns a list of relation names which should be considered
        mandatory for the Legend service to be started.

        Returns:
            list(str) of relation names denoting mandatory relations.
        """
        return [
            cls._get_legend_db_relation_name(),
            cls._get_legend_gitlab_relation_name()]

    def _get_core_legend_service_configs(
            self, legend_db_credentials, legend_gitlab_credentials):
        """Returns a list of config files required for the Legend service.
        This method will only get called once both the GitLab and Legend DB
        relation datas are present, so the arguments are non-void guaranteed.

        Args:
            legend_db_credentials: dict of the form:
            {
                "uri": "<replica set URI (with user/pass, no DB name)>",
                "username": "<username>",
                "password": "<password>",
                "database": "<database name>"
            }

            legend_gitlab_credentials: dict of the form:
            {
                "client_id": "<client_id>",
                "client_secret": "<client_secret>"
                "openid_discovery_url": "<URL>",
                "gitlab_host": "<GitLab hostname or IP>",
                "gitlab_port": <port>,
                "gitlab_scheme": "<http/https>",
                "gitlab_host_cert_b64": "<base64 DER certificate>"
            }

        Returns:
            A `model.BlockedStatus` on error, or a dict of the following form:
            {
                "/path/to/config-file-1.txt": "<contents of config file>",
                "/legend.json": '{"example": "config"}'
            }
        """
        raise NotImplementedError("No Core Legend service configs defined.")

    def _get_service_configs(self, relations_data):
        """Overrides BaseFinosLegendCharm._get_service_configs() to add
        explicit checks/waits for the GitLab and DB relations data.
        """
        # NOTE(aznashwan): the services do not support scaling and thus
        # there should only ever be one relation with each supporting
        # service, so we pass `None` as the `relation_id` to the
        # various relation consumer instances:
        legend_db_credentials = None
        try:
            legend_db_credentials = (
                self._legend_db_consumer.get_legend_database_creds(None))
        except Exception:
            logger.error(
                "Exception occurred while fetching DB relation data: %s",
                traceback.format_exc())
            return model.BlockedStatus("failed to fetch DB relation data")
        legend_gitlab_credentials = None
        try:
            legend_gitlab_credentials = (
                self._legend_gitlab_consumer.get_legend_gitlab_creds(
                    None))
        except Exception:
            logger.error(
                "Exception occurred while fetching GitLab relation data: %s",
                traceback.format_exc())
            return model.BlockedStatus("failed to fetch GitLab relation data")

        return self._get_core_legend_service_configs(
            legend_db_credentials, legend_gitlab_credentials)

    def _on_db_relation_joined(
            self, _: charm.RelationJoinedEvent) -> None:
        self._refresh_charm_status()

    def _on_db_relation_changed(
            self, _: charm.RelationChangedEvent) -> None:
        self._refresh_charm_status()

    def _on_db_relation_broken(
            self, _: charm.RelationBrokenEvent) -> None:
        self._refresh_charm_status()

    def _on_legend_gitlab_relation_joined(
            self, event: charm.RelationJoinedEvent) -> None:
        redirect_uris = self._get_legend_gitlab_redirect_uris()
        legend_gitlab.set_legend_gitlab_redirect_uris_in_relation_data(
            event.relation.data[self.app], redirect_uris)

    def _on_legend_gitlab_relation_changed(
            self, _: charm.RelationChangedEvent) -> None:
        self._refresh_charm_status()

    def _on_legend_gitlab_relation_broken(
            self, _: charm.RelationBrokenEvent) -> None:
        self._refresh_charm_status()

    def _get_legend_gitlab_certificate(self):
        """Returns an `OpenSSL.X509` instance of the certificate from
        the GitLab relation data."""
        gitlab_creds = (
            self._legend_gitlab_consumer.get_legend_gitlab_creds(None))
        if not gitlab_creds or 'gitlab_host_cert_b64' not in gitlab_creds:
            return None
        return gitlab_creds['gitlab_host_cert_b64']
