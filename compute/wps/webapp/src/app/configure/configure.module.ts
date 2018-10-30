import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SharedModule } from '../shared/shared.module';
import { DndModule } from 'ng2-dnd';

import { DomainComponent } from './domain.component';
import { AxisComponent } from './axis.component';
import { ParameterComponent } from './parameter.component';
import { MapComponent } from './map.component';
import { RegridComponent } from './regrid.component';
import { ConfigureComponent } from './configure.component';
import { EnumToArrayPipe } from './domain.component';
import { FilterPipe } from './filter.pipe';
import { FilenamePipe } from './filename.pipe';
import { WorkflowComponent } from './workflow.component';
import { ProcessConfigureComponent } from './process-configure.component';

import { ConfigureService } from './configure.service';

import { ConfigureRoutingModule } from './configure-routing.module';

@NgModule({
  imports: [ 
    CommonModule,
    FormsModule,
    ConfigureRoutingModule,
    SharedModule,
    DndModule,
  ],
  declarations: [
    DomainComponent,
    ConfigureComponent,
    AxisComponent,
    ParameterComponent,
    MapComponent,
    RegridComponent,
    EnumToArrayPipe,
    FilterPipe,
    FilenamePipe,
    WorkflowComponent,
    ProcessConfigureComponent,
  ],
  exports: [],
  providers: [
    ConfigureService,
    EnumToArrayPipe,
    FilterPipe,
    FilenamePipe,
  ]
})
export class ConfigureModule { }
