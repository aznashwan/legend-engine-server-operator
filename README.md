# FINOS Legend Engine Operator

## Description

The Legend Operators package the core [FINOS Legend](https://legend.finos.org)
components for quick and easy deployment of a Legend stack.

This repository contains a [Juju](https://juju.is/) Charm for
deploying the Engine, the model-centric metadata server for Legend.

The full Legend solution can be installed with the dedicated
[Legend bundle](https://charmhub.io/finos-legend-bundle).


## Usage

The Engine Operator can be deployed by running:

```sh
$ juju deploy finos-legend-engine-k8s --channel=edge
```


## Relations

The standalone Engine will initially be blocked, and will require being later
related to the [Legend Database Operator](https://github.com/canonical/finos-legend-db-operator),
as well as the [Legend GitLab Integrator](https://github.com/canonical/finos-legend-gitlab-integrator).

```sh
$ juju deploy finos-legend-db-k8s finos-legend-gitlab-integrator-k8s
$ juju relate finos-legend-engine-k8s finos-legend-db-k8s
$ juju relate finos-legend-engine-k8s finos-legend-gitlab-integrator-k8s
# If relating to Legend Studio:
$ juju relate finos-legend-engine-k8s finos-legend-studio-k8s
```

## OCI Images

This charm by default uses the latest version of the
[finos/legend-engine-server](https://hub.docker.com/r/finos/legend-engine-server) image.
