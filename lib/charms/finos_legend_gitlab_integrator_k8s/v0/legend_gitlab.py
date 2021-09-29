#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.

""" Charm library formalizing FINOS GitLab relations. """

import json
import logging

from ops import framework


# The unique Charmhub library identifier, never change it
LIBID = "c31b1a71091248029dbc029989f35343"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 0

REQUIRED_LEGEND_GITLAB_CREDENTIALS = [
    "client_id", "client_secret", "openid_discovery_url",
    "gitlab_host", "gitlab_port", "gitlab_scheme",
    "gitlab_host_cert_b64"]

logger = logging.getLogger(__name__)


def _validate_legend_gitlab_credentials(creds):
    """Raises a ValueError if the provided gitlab creds isn't a dict
    or has missing keys/void values.
    """
    if not isinstance(creds, dict) and any([
            not creds.get(k)
            for k in REQUIRED_LEGEND_GITLAB_CREDENTIALS]):
        raise ValueError(
            "Improper gitlab credentials provided, must be a dict with "
            "the following keys: %s. Got: %r" % (
                REQUIRED_LEGEND_GITLAB_CREDENTIALS, creds))
    return True


def set_legend_gitlab_creds_in_relation_data(
        relation_data, creds, validate_creds=True):
    """Set connection data for GitLab from the provided relation data.

    Args:
        relation_data: Data of the relation to set the info into.
        validate_creds: Whether or not to check the structure of the data.

    Returns:
        True if the provided creds were successfully set.

    Raises:
        ValueError: if the provided credentials are misformatted.
    """
    try:
        _validate_legend_gitlab_credentials(creds)
    except ValueError:
        if not validate_creds:
            raise
        logger.warning(
            "Setting incorrectly structured GitLab relation data '%s'",
            creds)
    relation_data["legend-gitlab-connection"] = json.dumps(creds)
    return True


def _validate_legend_gitlab_redirect_uris(redirect_uris):
    """Raises a ValueError if the provided rediret_uris is not an
    iterable list and/or has non-string elements.
    """
    if not isinstance(redirect_uris, list) or not all([
            isinstance(elem, str) for elem in redirect_uris]):
        raise ValueError(
            "Improper redirect_uris parameter provided. Must be a list of "
            "strings. Got: %r" % redirect_uris)
    return True


def set_legend_gitlab_redirect_uris_in_relation_data(
        relation_data, redirect_uris):
    """Set connection data for GitLab from the provided relation data.

    Args:
        redirect_uris: list of strings of redirect URLs for this service.

    Returns:
        True if the provided creds are of a valid structure, else False.

    Raises:
        ValueError: if the provided redirect URI parameter is incorrect.
    """
    _validate_legend_gitlab_redirect_uris(redirect_uris)
    relation_data["legend-gitlab-redirect-uris"] = json.dumps(redirect_uris)
    return True


class LegendGitlabConsumer(framework.Object):
    def __init__(self, charm, relation_name='finos-gitlab'):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def set_service_url(self, relation_id, service_type, service_url):
        """Sets the service URL parameters in the relation for the
        GitLab integrator to consume.

        Args:
            relation_id: ID of the relation to set URL data from.
            service_url: string URL of the service to set.

        Returns:

        Raises:
            TooManyRelatedAppsError if relation id is not provided and
            multiple relation of the same name are present.
        """

    def get_legend_gitlab_creds(self, relation_id):
        """Get GitLab OAuth connection data from the provided relation.

        Args:
            relation_id: ID of the relation to fetch data from.

        Returns:
            Dictionary with the following structure:
            {
                "client_id": "<client_id>",
                "client_secret": "<client_secret>"
            }

        Raises:
            TooManyRelatedAppsError if relation id is not provided and
            multiple relation of the same name are present.
            ValueError: if the GitLab creds are misformatted.
        """
        relation = self.framework.model.get_relation(
            self.relation_name, relation_id)
        relation_data = relation.data[relation.app]

        creds = None
        creds_data = relation_data.get("legend-gitlab-connection", "{}")
        try:
            creds = json.loads(creds_data)
        except Exception as ex:
            raise ValueError(
                "Could not deserialize Legend GitLab creds JSON: %s." % (
                    creds_data)) from ex

        if not creds:
            return {}

        _validate_legend_gitlab_credentials(creds)

        return creds

    def get_legend_redirect_uris(self, relation_id):
        """Get GitLab redirect URIs from the provided relation.

        Args:
            relation_id: ID of the relation to fetch data from.

        Returns:
            List of strings of redirect URIs.

        Raises:
            TooManyRelatedAppsError if relation id is not provided and
            multiple relation of the same name are present.
            ValueError: if the GitLab redirect URIs are misformatted.
        """
        relation = self.framework.model.get_relation(
            self.relation_name, relation_id)
        relation_data = relation.data[relation.app]

        redirect_uris = None
        redirect_uris_data = relation_data.get(
            "legend-gitlab-redirect-uris", "[]")
        try:
            redirect_uris = json.loads(redirect_uris_data)
        except Exception as ex:
            raise ValueError(
                "Could not deserialize Legend GitLab URIs JSON: %s." % (
                    redirect_uris_data)) from ex
        _validate_legend_gitlab_redirect_uris(redirect_uris)

        return redirect_uris
