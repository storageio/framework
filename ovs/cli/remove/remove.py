# Copyright (C) 2019 iNuron NV
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

import click
from ..commands import OVSCommand


@click.command('node', help='Remove node from cluster', command_parameter_help='<ip>', cls=OVSCommand)
@click.argument('IP')
@click.option('--force-yes', required=False, default=False, is_flag=True)
def remove_node(ip, force_yes):
    from ovs.lib.noderemoval import NodeRemovalController
    NodeRemovalController.remove_node(node_ip=str(ip), silent=force_yes)

