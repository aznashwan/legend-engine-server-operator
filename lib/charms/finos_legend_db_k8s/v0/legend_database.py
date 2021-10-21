# Copyright 2021 Canonical
# See LICENSE file for licensing details.

"""Module defining Legend DB consumer class and helpers."""

import json
import logging

from ops import framework

# The unique Charmhub library identifier, never change it
LIBID = "02ed64badd5941c5acfdae546b0f79a2"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

LEGEND_DB_RELATION_DATA_KEY = "legend-db-connection"
REQUIRED_LEGEND_DATABASE_CREDENTIALS = [
    "username", "password", "database", "uri"]

logger = logging.getLogger(__name__)


def get_database_connection_from_mongo_data(
        mongodb_consumer_data, mongodb_databases):
    """Returns a dict with Mongo connection info for Legend components
    just like `LegendDatabaseConsumer.get_legend_database_creds()`.

    Args:
        mongodb_consumer_connection: connection data as returned by
            charms.mongodb_k8s.v0.mongodb.MongoConsumer.connection()
            Should be a dict of the following structure: {
                "username": "user",
                "password": "pass",
                "replica_set_uri": "Replica set URI with user/pass/login DB"
            }
        mongodb_databases: List of database names as returned by
            charms.mongodb_k8s.v0.mongodb.MongoConsumer.databases()

    Returns:
        Dictionary with the following structure:
        {
            "uri": "<replica set URI (with user/pass, no DB name)>",
            "username": "<username>",
            "password": "<password>",
            "database": "<database name>"
        }
    """
    if not isinstance(mongodb_consumer_data, dict):
        logger.warning("MongoDB consumer data not a dict.")
        return {}
    missing_keys = [
        k for k in ["username", "password", "replica_set_uri"]
        if not mongodb_consumer_data.get(k)]
    if missing_keys:
        logger.warning(
            "Following keys were missing from the MongoDB connection "
            "data provided: %s. Data was: %s",
            missing_keys, mongodb_consumer_data)
        return {}
    if any([not isinstance(v, str) for v in mongodb_consumer_data.values()]):
        logger.warning(
            "Not all mongoDB database values are strings: %s", mongodb_consumer_data)
        return {}

    if not isinstance(mongodb_databases, list) or not (
            all([isinstance(v, str) for v in mongodb_databases])):
        logger.warning(
            "MongoDB databases must be a list of strings, not: %s",
            mongodb_databases)
        return {}
    if not mongodb_databases:
        logger.info("No Mongo databases provided by the MongoConsumer.")
        return {}

    uri = mongodb_consumer_data['replica_set_uri']
    # NOTE: we remove the trailing database from the URI:
    split_uri = [
        elem for elem in uri.split('/')[:-1]
        # NOTE: filter any empty strings resulting from double-slashes:
        if elem]
    if not len(split_uri) > 1:
        logger.warning("Failed to process DB URI: %s", uri)
        return {}
    # NOTE: schema prefix needs two slashes added back:
    uri = "%s//%s" % (
        split_uri[0], "/".join(split_uri[1:]))

    res = {
        "uri": uri,
        "username": mongodb_consumer_data['username'],
        "password": mongodb_consumer_data['password'],
        "database": mongodb_databases[0]}

    if not _validate_legend_database_credentials(res):
        logger.warning("Failed to validate legend creds.")
        return {}

    return res


def set_legend_database_creds_in_relation_data(relation_data, creds):
    """Set connection data for MongoDB from the provided relation data.

    Args:
        relation_data: Data of the relation to set the info into.

    Returns:
        True if the provided creds are of a valid structure, else False.
    """
    if not _validate_legend_database_credentials(creds):
        return False
    relation_data[LEGEND_DB_RELATION_DATA_KEY] = json.dumps(creds)
    return True


def _validate_legend_database_credentials(creds):
    """Returns True/False depending on whether the provided Legend
    database credentials dict contains all the required fields.
    """
    if not isinstance(creds, dict) or any([
            not isinstance(creds.get(k), str) for k in REQUIRED_LEGEND_DATABASE_CREDENTIALS]):
        return False
    return True


class LegendDatabaseConsumer(framework.Object):
    """Class which facilitates reading Legend DB creds from relation data."""
    def __init__(self, charm, relation_name="legend-db"):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def get_legend_database_creds(self, relation_id):
        """Get connection data for MongoDB from the provided relation.

        Args:
            relation_id: ID of the relation to fetch data from.

        Returns:
            Dictionary with the following structure:
            {
                "uri": "<replica set URI (with user/pass, no DB name)>",
                "username": "<username>",
                "password": "<password>",
                "database": "<database name>"
            }

        Raises:
            TooManyRelatedAppsError if relation id is not provided and
            multiple relation of the same name are present.
        """
        relation = self.framework.model.get_relation(
            self.relation_name, relation_id)
        if not relation:
            logger.warning(
                "No relation of name '%s' and ID '%s' was found.",
                self.relation_name, relation_id)
            return {}
        relation_data = relation.data[relation.app]

        creds_data = relation_data.get(LEGEND_DB_RELATION_DATA_KEY, "{}")
        try:
            creds = json.loads(creds_data)
        except Exception as ex:
            logger.warning(
                "Could not deserialize Legend DB creds JSON: %s. Error "
                "was: %s", creds_data, str(ex))
            return {}
        if not _validate_legend_database_credentials(creds):
            logger.warning("Invalid DB creds in relation: %s", creds)
            return {}

        return creds
