import json
import logging
import os
import uuid

import cwt
import zarr
from redis.connection import ConnectionPool

from compute_tasks import base
from compute_tasks import mapper
from compute_tasks import wps_state_api
from compute_tasks import WPSError

logger = logging.getLogger(__name__)


class BaseContext:
    def __init__(
        self,
        variable,
        domain,
        operation,
        extra=None,
        gdomain=None,
        gparameters=None,
        output=None,
        sorted=None,
        input_var_names=None,
        **kwargs,
    ):
        BaseContext.resolve_dependencies(
            variable, domain, operation, gdomain, gparameters
        )

        self._variable = variable
        self._domain = domain
        self._operation = operation
        self.extra = extra or {}
        self.gdomain = gdomain
        self.gparameters = gparameters or {}
        self._output = output or {}
        self._sorted = sorted or []
        self.input_var_names = input_var_names or {}
        self._delayed = []

    @classmethod
    def from_data_inputs(cls, identifier, data_inputs, **kwargs):
        variable, domain, operation = cls.decode_data_inputs(data_inputs)

        map = mapper.Mapper.from_config("/etc/config/mapping.json")

        logger.info("Loaded mapper")

        # Attempt to find local match
        for x in list(variable.keys()):
            try:
                local_path = map.find_match(variable[x].uri)
            except mapper.MatchNotFoundError:
                pass
            else:
                variable[x].uri = local_path

        try:
            root_op = [
                x for x in operation.values() if x.identifier == identifier
            ][0]
        except IndexError:
            raise WPSError("Error finding operation {!r}", identifier)

        gdomain = None

        gparameters = None

        if identifier == "CDAT.workflow":
            # Remove workflow operation servers no further purpose
            operation.pop(root_op.name)

            gdomain = domain.get(root_op.domain, None)

            gparameters = root_op.parameters

        ctx = cls(
            variable=variable,
            domain=domain,
            operation=operation,
            gdomain=gdomain,
            gparameters=gparameters,
            **kwargs,
        )

        return ctx

    def to_dict(self):
        data = {
            "gdomain": self.gdomain,
            "gparameters": self.gparameters,
            "variable": self._variable,
            "domain": self._domain,
            "operation": self._operation,
            "output": self._output,
            "sorted": self._sorted,
            "input_var_names": self.input_var_names,
            "extra": self.extra,
        }

        return data

    @staticmethod
    def decode_data_inputs(data_inputs):
        items = {}

        for id in ("variable", "domain", "operation"):
            try:
                data = data_inputs[id]
            except KeyError as e:
                raise WPSError('Missing required data input "{!s}"', e)

            if id == "variable":
                data = [cwt.Variable.from_dict(x) for x in data]

                items["variable"] = dict((x.name, x) for x in data)
            elif id == "domain":
                data = [cwt.Domain.from_dict(x) for x in data]

                items["domain"] = dict((x.name, x) for x in data)
            elif id == "operation":
                data = [cwt.Process.from_dict(x) for x in data]

                items["operation"] = dict((x.name, x) for x in data)

        return items["variable"], items["domain"], items["operation"]

    @staticmethod
    def resolve_dependencies(
        variable, domain, operation, gdomain=None, gparameters=None
    ):
        for item in operation.values():
            inputs = []

            for x in item.inputs:
                if x in variable:
                    inputs.append(variable[x])
                elif x in operation:
                    inputs.append(operation[x])
                else:
                    raise WPSError("Error finding input {!s}", x)

            item.inputs = inputs

            # Default to global domain
            item.domain = domain.get(item.domain, gdomain)

            if gparameters is not None:
                item.add_parameters(**gparameters)

    @property
    def sorted(self):
        for x in self._sorted:
            yield self._operation[x]

    @property
    def delayed(self):
        return self._delayed

    def add_delayed(self, delayed):
        self._delayed.append(delayed)

    def set_provenance(self, ds):
        frontend = self.extra.get("provenance", {})

        for x, y in frontend.items():
            ds.attrs[f"provenance.{x}"] = str(y)

        if "CONTAINER_IMAGE" in os.environ:
            ds.attrs["provenance.container_image"] = os.environ[
                "CONTAINER_IMAGE"
            ]

    def output_ops(self):
        out_deg = dict(
            (x, self.node_out_deg(y)) for x, y in self._operation.items()
        )

        return [self._operation[x] for x, y in out_deg.items() if y == 0]

    def interm_ops(self):
        out_deg = dict(
            (x, self.node_out_deg(y)) for x, y in self._operation.items()
        )

        return [self._operation[x] for x, y in out_deg.items() if y > 0]

    def node_in_deg(self, node):
        return len([x for x in node.inputs if x.name in self._operation])

    def node_out_deg(self, node):
        return len(
            [
                x
                for x, y in self._operation.items()
                if any(node.name == z.name for z in y.inputs)
            ]
        )

    def find_neighbors(self, node):
        return [
            x
            for x, y in self._operation.items()
            if node in [x.name for x in y.inputs]
        ]

    def topo_sort(self):
        in_deg = dict(
            (x, self.node_in_deg(y)) for x, y in self._operation.items()
        )

        neigh = dict((x, self.find_neighbors(x)) for x in in_deg.keys())

        queue = [x for x, y in in_deg.items() if y == 0]

        while queue:
            next = queue.pop(0)

            self._sorted.append(next)

            operation = self._operation[next]

            var_names = set()

            for x in operation.inputs:
                if isinstance(x, cwt.Variable):
                    var_names.add(x.var_name)
                else:
                    for y in self.input_var_names[x.name]:
                        var_names.add(y)

            self.input_var_names[next] = list(var_names)

            yield operation, self.input_var_names[next]

            process = base.get_process(operation.identifier)

            params = process._get_parameters(operation)

            rename = params.get("rename")

            if rename is not None:
                for x, y in zip(rename[::2], rename[1::2]):
                    var_names.add(y)

                    var_names.remove(x)

                self.input_var_names[next] = list(var_names)

            for x in neigh[next]:
                in_deg[x] -= 1

                if in_deg[x] == 0:
                    queue.append(x)

    def get_base_path(self):
        if (
            "output_path" in self.extra
            and self.extra["output_path"] is not None
        ):
            base_path = self.extra["output_path"]
        else:
            base_path = os.path.join(
                os.environ["DATA_PATH"], str(self.user), str(self.job)
            )

        return base_path

    def generate_local_path(self, filename=None):
        if filename is None:
            filename = f"{uuid.uuid4()!s}.nc"

        base_path = self.get_base_path()

        if not os.path.exists(base_path):
            os.makedirs(base_path)

        return os.path.join(base_path, filename)

    def build_output(
        self, mime_type, filename=None, var_name=None, name=None
    ):
        thredds_url = os.environ["THREDDS_URL"].rstrip("/")

        local_path = self.generate_local_path(filename=filename)

        common = os.path.commonpath([os.environ["DATA_PATH"], local_path])

        relative_path = local_path.replace(common, "")

        self._output[local_path] = cwt.Variable(
            f"{thredds_url}{relative_path}",
            var_name,
            name=name,
            mime_type=mime_type,
        )

        return local_path


class LocalContext(BaseContext):
    def __init__(self, **kwargs):
        self.job = 0
        self.user = 0
        self.process = 0
        self.status = 0

        super().__init__(**kwargs)

        self.store = zarr.DirectoryStore("/cache")

    def started(self):
        logger.info("Process started")

    def succeeded(self):
        for x, y in self._output.items():
            size = os.stat(x).st_size / 1e6

            self.output(x, y.uri, size)

            logger.info(f"Output {y!r}")

    def failed(self, exception):
        logger.info(f"Process failed {exception}")

    def message(self, message, percent=None):
        logger.info(f"{message} {percent}")

    def output(self, local, remote, size):
        logger.info(f"Local file {local!r} remote {remote!r} size {size!r}")

    def to_dict(self):
        pass


class OperationContext(BaseContext):
    def __init__(self, job, user, process, status, **kwargs):
        self.job = job
        self.user = user
        self.process = process
        self.status = status

        self.store = OperationContext.create_cache_store()

        super().__init__(**kwargs)

        self.state = wps_state_api.WPSStateAPI()

    @staticmethod
    def create_cache_store():
        store = None
        cache_type = os.environ.get("CACHE_TYPE", None)

        if cache_type == "redis":
            redis_url = os.environ["REDIS_URL"]
            pool = ConnectionPool.from_url(redis_url)

            store = zarr.RedisStore(connection_pool=pool)
        elif cache_type == "directory":
            store_dir = os.environ["CACHE_DIRECTORY"]

            if not os.path.exists(store_dir):
                os.makedirs(store_dir)

            store = zarr.DirectoryStore(store_dir)

        if store is not None:
            cache_size = int(os.environ.get("CACHE_SIZE", "100"))

            store = zarr.LRUStoreCache(store, cache_size*1e6)

        return store

    @classmethod
    def from_data_inputs(cls, identifier, data_inputs, **kwargs):
        return super().from_data_inputs(identifier, data_inputs, **kwargs)

    def started(self):
        self.status = self.state.started(self.job)

    def succeeded(self):
        for local, variable in self._output.items():
            size = os.stat(local).st_size / 1e6

            self.output(local, variable.uri, size)

        self.status = self.state.succeeded(
            self.job, json.dumps([x.to_dict() for x in self._output.values()])
        )

    def failed(self, exception):
        self.status = self.state.failed(self.job, exception)

    def message(self, message, percent=None):
        self.state.message(self.status, message, percent)

    def output(self, local, remote, size):
        self.state.output_create(self.job, local, remote, size)

    def __getstate__(self):
        state = self.__dict__.copy()

        del state["store"]

        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

        self.store = OperationContext.create_cache_store()

    def to_dict(self):
        store = super().to_dict()

        store["job"] = self.job
        store["user"] = self.user
        store["process"] = self.process
        store["status"] = self.status

        return store
