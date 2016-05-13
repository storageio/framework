# Copyright 2016 iNuron NV
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
PMachine module
"""

from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from ovs.dal.lists.storagedriverlist import StorageDriverList
from ovs.dal.lists.vmachinelist import VMachineList
from ovs.dal.hybrids.storagedriver import StorageDriver
from ovs.lib.vdisk import VDiskController
from backend.decorators import required_roles, load, return_list, return_object, return_plain, return_task, log


class StorageDriverViewSet(viewsets.ViewSet):
    """
    Information about StorageDrivers
    """
    permission_classes = (IsAuthenticated,)
    prefix = r'storagedrivers'
    base_name = 'storagedrivers'

    @log()
    @required_roles(['read'])
    @return_list(StorageDriver)
    @load()
    def list(self):
        """
        Overview of all StorageDrivers
        """
        return StorageDriverList.get_storagedrivers()

    @log()
    @required_roles(['read'])
    @return_object(StorageDriver)
    @load(StorageDriver)
    def retrieve(self, storagedriver):
        """
        Load information about a given StorageDriver
        """
        return storagedriver

    @action()
    @log()
    @required_roles(['read'])
    @return_plain()
    @load(StorageDriver)
    def can_be_deleted(self, storagedriver):
        """
        Checks whether a Storage Driver can be deleted
        """
        result = True
        storagerouter = storagedriver.storagerouter
        storagedrivers_left = len([sd for sd in storagerouter.storagedrivers if sd.guid != storagedriver.guid])
        pmachine = storagerouter.pmachine
        vmachines = VMachineList.get_customer_vmachines()
        vpools_guids = [vmachine.vpool_guid for vmachine in vmachines if vmachine.vpool_guid is not None]
        pmachine_guids = [vmachine.pmachine_guid for vmachine in vmachines]
        vpool = storagedriver.vpool

        if storagedrivers_left is False and pmachine.guid in pmachine_guids and vpool.guid in vpools_guids:
            result = False
        if any(vdisk for vdisk in vpool.vdisks if vdisk.storagedriver_id == storagedriver.storagedriver_id):
            result = False
        return result

    @action()
    @required_roles(['read', 'write'])
    @return_task()
    @load(StorageDriver)
    def create_new_disk(self, storagedriver, diskname, size):
        """
        Create a new empty vdisk - including the volume in the backend
        :param diskname: Name of the new vdisk
        :param size: Size in GB
        :param storagedriver_guid: Guid of the storagedriver holding the vdisk
        """
        return VDiskController.create_new.delay(diskname=diskname,
                                                size=size,
                                                storagedriver_guid=storagedriver.guid)
