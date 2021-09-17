# Copyright 2021 Canonical
# See LICENSE file for licensing details.

import json
import logging

from ops import framework


# The unique Charmhub library identifier, never change it
LIBID = "431732f8afb641a3a5a38e5c5d01ee11"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 0

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
    missing_keys = [
        k for k in ["username", "password", "replica_set_uri"]
        if not mongodb_consumer_data.get(k)]
    if missing_keys:
        logger.warning(
            "Following keys were missing from the MongoDB connection "
            "data provided: %s. Data was: %s",
            missing_keys, mongodb_consumer_data)
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
    relation_data["legend-db-connection"] = json.dumps(creds)
    return True


def _validate_legend_database_credentials(creds):
    """Returns True/False depending on whether the provided Legend
    database credentials dict contains all the required fields."""
    if any([not creds.get(k)
            for k in REQUIRED_LEGEND_DATABASE_CREDENTIALS]):
        return False
    return True


class LegendDatabaseConsumer(framework.Object):
    def __init__(self, charm, relation_name="legend-db"):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def get_legend_database_creds(self, relation_id):
        """Get connection data for MongoDB from the provided relation data.

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
        """
        relation = self.framework.model.get_relation(
            self.relation_name, relation_id)
        relation_data = relation.data[relation.app]

        creds_data = relation_data.get("legend-db-connection", "{}")
        try:
            creds = json.loads(creds_data)
        except Exception as ex:
            logger.warning(
                "Could not deserialize Legend DB creds JSON: %s. Error "
                "was: %s", creds_data, str(ex))
            return {}
        if not _validate_legend_database_credentials(creds):
            return {}

        return creds
