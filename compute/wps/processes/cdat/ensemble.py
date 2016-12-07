import cdms2
from cdms2.selectors import timeslice
import esgf
import os
from pywps import config
import uuid

from wps import logger
from wps.processes import esgf_operation
from wps.processes.esgf_operation import create_from_def

class CDATEnsemble(esgf_operation.ESGFOperation):
    KNOWN_GRIDS = {
        't21': {
            'func': cdms2.createGaussianGrid,
            'args': [32,]
        },
        't42': {
            'func': cdms2.createGaussianGrid,
            'args': [128,]
        }
    }

    def __init__(self):
        super(CDATEnsemble, self).__init__()

    @property
    def title(self):
        return 'CDAT Ensemble'

        
    def __call__(self, data_manager, status):
        # First dodsrc thing just in case

        data_manager.metadata(self.input()[0])

        output_path = config.getConfigValue('server', 'outputPath', '/var/wps')
        new_file_name = '%s.nc' % (str(uuid.uuid4()),)
        new_file = os.path.join(output_path, new_file_name)

        # Target grid
        target_grid = None

        gridder = self.parameter('gridder', required=False)
        status("GRIDDER: %s" % repr(gridder))
        # Build the target grid if gridder is passed to the operation
        if gridder:
            tool="esmf"
            method="linear"
            status("GRIIDER EXTRACT: %s, %s" % (gridder.tool,gridder.method))
            if isinstance(gridder.grid, esgf.Domain):
                target_grid = self._create_grid_from_domain(gridder.grid)
                tool = gridder.tool
                method = gridder.method
            elif isinstance(gridder.grid, esgf.Variable):
                status("GRIDDER FROM VAR %s" % gridder.grid)
                with cdms2.open(gridder.grid.uri, 'r') as grid_file:
                    var = grid_file[gridder.grid.var_name]
                    target_grid = var.getGrid()
            elif isinstance(gridder.grid, (str, unicode)):
                try:
                    status("KNOWN GRIDS %s" % self.KNOWN_GRIDS)
                    grid = self.KNOWN_GRIDS[gridder.grid.lower()]
                    status("SELECTED GRIIIIIDDDDDDDDD %s" % grid)
                    tool = gridder.tool
                    method = gridder.method
                except KeyError:
                    raise esgf.WPSServerError('Unknown target grid %s' %
                                              (gridder.grid,))

                target_grid = grid['func'](*grid['args'])
                status("CREATTTTTREEEEEDDDDD GRID %s" % target_grid)
            else:
                raise esgf.WPSServerError(
                    'Unknown value passed as target grid %r' % (gridder.grid,))
        
        fo = cdms2.open(new_file,"w")
        step = 200
        # Figures out shape (from first file)
        v=self.input()[0]
        f=cdms2.open(v.uri)
        V=f[v.var_name]
        sh =V.shape
        f.close()
        n = sh[0]
        out = None
        for i in range(0,n,step):
            for iv, v in enumerate(self.input()):
                status("LOOKING AT FILE: %s" % v.uri)
                f=cdms2.open(v.uri)
                V = f(v.var_name,slice(i,i+step)) 
                if target_grid is not None:
                    status("REGRIDDING: %s, %s, %s" % (target_grid,tool,method))
                    status("SHPE B4: %s" % str(V.shape))
                    V=V.regrid(target_grid,regridTool=tool,regridMethod=method)
                    status("SHPE AFTER: %s" % str(V.shape))
                if iv==0:
                    out = V
                    vnm = v.var_name
                else:
                    out[:]+=V[:]
            out/=(iv+1)
            fo.write(out,id=vnm)
        fo.close()
        dap_url = self.create_dap_url(new_file_name)
        self.set_output(dap_url, vnm) 




    def __call_old___(self, data_manager, status):
        var = [x for x in self.input() if isinstance(x, esgf.Variable)]

        var_name = None

        if len(var):
            var_name = var[0].var_name

        src = [data_manager.metadata(x) for x in var
               if isinstance(x, esgf.Variable)]

        # Find inputs that are operations and execute them appending the
        # input list
        # NOTE this will be moved into workflow at some point
        input_ops = [x for x in self.input() if isinstance(x, esgf.Operation)]

        for inp in input_ops:
            logger.debug('Executing child operation %s', inp.identifier)

            op = create_from_def(inp)

            if self.domain:
                op.data.domain = self.domain

            op.__call__(data_manager, status)

            src.append(data_manager.metadata(op.output))

            # If we couldn't get the variable name from source files,
            # take the first name from input operation outputs
            if not var_name:
                var_name = op.output.var_name

        # assumed all files are from the same model
        # TODO add some validation
        time = [x.getTime() for x in src]

        temporal, spatial = self._gen_domains(src[0].getAxisList(),
                                              time[0],
                                              self.domain)
        
        logger.debug('Spatial domain %r', spatial)

        start, end, step = self._convert_temporal_domain(time[0], temporal)

        logger.debug('Temporal domain: start=%s stop=%s step=%s', start, end, step)

        new_file_name = '%s.nc' % (str(uuid.uuid4()),)
        new_file_path = config.getConfigValue('server', 'outputPath', '/var/wps')
        new_file = os.path.join(new_file_path, new_file_name)

        logger.debug('Writing file to %s', new_file)

        fout = cdms2.open(new_file, 'w')

        denom = len(src)

        target_grid = None

        gridder = self.parameter('gridder', required=False)

        # Build the target grid if gridder is passed to the operation
        if gridder:
            if isinstance(gridder.grid, esgf.Domain):
                target_grid = self._create_grid_from_domain(gridder.grid)
            elif isinstance(gridder.grid, esgf.Variable):
                with cdms2.open(gridder.grid.uri, 'r') as grid_file:
                    var = grid_file[gridder.grid.var_name]

                    target_grid = var.getGrid()
            elif isinstance(gridder.grid, (str, unicode)):
                try:
                    grid = self.KNOWN_GRIDS[gridder.grid.lower()]
                except KeyError:
                    raise esgf.WPSServerError('Unknown target grid %s' %
                                              (gridder.grid,))

                target_grid = grid['func'](*grid['args'])
            else:
                raise esgf.WPSServerError(
                    'Unknown value passed as target grid %r' % (gridder.grid,))

            logger.debug('Target grid %r', target_grid)

        for i in xrange(start, end, step):
            logger.debug('Averaging time slice %s', i)

            # Grab indexed time slice relative to it's own timeline
            # Apply spatial subset if present
            data = [x(time=time[j][i], **spatial) for j, x in enumerate(src)]

            # Regrid slice
            if target_grid:
                data = [x.regrid(target_grid,
                                 regridTool=gridder.tool,
                                 regridMethod=gridder.method) for x in data]

            data_sum = reduce(lambda x, y: x + y, data)

            data_avg = data_sum / denom

            fout.write(data_avg, id=var_name) 

            del data[:]
            del data_sum
            del data_avg

        fout.close()

        dap_url = self.create_dap_url(new_file_name)

        self.set_output(dap_url, var_name)

    def _creaate_grid_from_domain(self, domain):
        lat = self._find_dimension(domain, ('latitude', 'lat'))

        lon = self._find_dimension(domain, ('longitude', 'lon'))

        if not lat.end or lat.end == lat.start:
            lat_start = lat.start
            lat_n = 1
            lat_delta = lat.step
        else:
            lat_start = lat.start
            lat_n = (lat.end - lat.start) / lat.step
            lat_delta = 0

        if not lon.end or lon.end == lon.start:
            lon_start = lon.start
            lon_n = 1
            lon_delta = lon.step
        else:
            lon_start = lon.start
            lon_n = (lon.end - lon.start) / lon.step
            lon_delta = 0

        return cdms2.createUnifromGrid(lat_start, lat_n, lat_delta,
                                       lon_start, lon_n, lon_delta)

    def _find_dimension(self, domain, dimension):
        try:
            return [x for x in domain.dimensions
                    if x.name in dimensions][0]
        except KeyError:
            raise esgf.WPSServerError('Missing dimension %r' % (dimensions,))

    def _convert_temporal_domain(self, time, domain):
        start = 0
        end = len(time)
        step = 1

        if domain:
            if domain.crs == esgf.Dimension.indices:
                start = domain.start
                end = domain.end
                step = domain.step
            elif domain.crs == esgf.Dimension.values:
                # Convert values in indices
                try:
                    start, end = time.mapInterval((str(domain.start),
                                                   str(domain.end)))
                except Exception as e:
                    raise esgf.WPSServerError(
                        'MapInterval failed for time domain "%r" error "%s"' %
                        (domain, e))

                step = domain.step
            else:
                raise esgf.WPSServerError('Unknown CRS for time domain "%r"' %
                                          (domain,))

        return start, end, step

    def _gen_domains(self, axes, time, domain):
        temporal = None
        spatial = {}

        if domain:
            for d in domain.dimensions:
                is_time = False

                # Check if the dimension is temporal
                for a in axes:
                    if (d.name == 'time' or
                            (d.name == a.id and a.isTime())):
                        is_time = True

                        temporal = d

                        break

                # Process spatial dimensions
                if not is_time:
                    if d.crs == esgf.Dimension.indices:
                        spatial[d.name] = slice(d.start, d.end)
                    elif d.crs == esgf.Dimension.values:
                        spatial[d.name] = (d.start, d.end)
                    else:
                        raise esgf.WPSServerError('Unknown CRS value "%s"' %
                                                  (temporal,))
    
        return temporal, spatial
