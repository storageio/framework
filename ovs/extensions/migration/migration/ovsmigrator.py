# Copyright (C) 2016 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of any kind.

"""
OVS migration module
"""


class OVSMigrator(object):
    """
    Handles all model related migrations
    """

    identifier = 'ovs'  # Used by migrator.py, so don't remove
    THIS_VERSION = 11

    def __init__(self):
        """ Init method """
        pass

    @staticmethod
    def migrate(previous_version, master_ips=None, extra_ips=None):
        """
        Migrates from a given version to the current version. It uses 'previous_version' to be smart
        wherever possible, but the code should be able to migrate any version towards the expected version.
        When this is not possible, the code can set a minimum version and raise when it is not met.
        :param previous_version: The previous version from which to start the migration
        :type previous_version: float
        :param master_ips: IP addresses of the MASTER nodes
        :type master_ips: list or None
        :param extra_ips: IP addresses of the EXTRA nodes
        :type extra_ips: list or None
        """

        _ = master_ips, extra_ips
        working_version = previous_version

        # From here on, all actual migration should happen to get to the expected state for THIS RELEASE
        if working_version < OVSMigrator.THIS_VERSION:
            # Adjustment of open file descriptors for Arakoon services to 8192
            from ovs.extensions.db.arakoon.ArakoonInstaller import ArakoonInstaller
            from ovs.extensions.generic.configuration import Configuration, NotFoundException
            from ovs.extensions.generic.sshclient import SSHClient
            from ovs.extensions.generic.system import System
            from ovs.extensions.services.service import ServiceManager

            local_sr = System.get_my_storagerouter()
            local_client = SSHClient(endpoint=local_sr, username='root')
            for cluster_name in list(Configuration.list('/ovs/arakoon')) + ['cacc']:
                # Retrieve metadata
                try:
                    metadata = ArakoonInstaller.get_arakoon_metadata_by_cluster_name(cluster_name=cluster_name)
                except NotFoundException:
                    metadata = ArakoonInstaller.get_arakoon_metadata_by_cluster_name(cluster_name=cluster_name, filesystem=True, ip=local_sr.ip)

                if metadata['internal'] is False:
                    continue

                cluster_name = metadata['cluster_name']
                service_name = ArakoonInstaller.get_service_name_for_cluster(cluster_name=cluster_name)
                configuration_key = '/ovs/framework/hosts/{0}/services/{1}'.format(local_sr.machine_id, service_name)
                if Configuration.exists(configuration_key) and ServiceManager.has_service(name=service_name, client=local_client):
                    # Rewrite the service file
                    service_params = Configuration.get(configuration_key)
                    startup_dependency = service_params['STARTUP_DEPENDENCY']
                    if startup_dependency == '':
                        startup_dependency = None
                    else:
                        startup_dependency = '.'.join(startup_dependency.split('.')[:-1])  # Remove .service from startup dependency
                    ServiceManager.add_service(name='ovs-arakoon',
                                               client=local_client,
                                               params=service_params,
                                               target_name='ovs-arakoon-{0}'.format(cluster_name),
                                               startup_dependency=startup_dependency,
                                               delay_registration=True)

                    # Let the update know that Arakoon needs to be restarted
                    # Inside `if Configuration.exists`, because useless to rapport restart if we haven't rewritten service file
                    run_file = '/opt/OpenvStorage/run/{0}.version'.format(service_name)
                    if local_client.file_exists(filename=run_file):
                        contents = local_client.file_read(run_file).strip()
                        if '=' in contents:
                            contents = ';'.join(['{0}-reboot'.format(part) for part in contents.split(';') if 'arakoon' in part])
                        else:
                            contents = '{0}-reboot'.format(contents)
                        # Add something to the version, which makes sure it no longer matches the actually installed version
                        local_client.file_write(filename=run_file, contents=contents)

        return OVSMigrator.THIS_VERSION
