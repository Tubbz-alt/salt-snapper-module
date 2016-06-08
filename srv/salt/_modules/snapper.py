# -*- coding: utf-8 -*-
'''
Module to manage filesystem snapshots with snapper
:depends: - ``dbus`` Python module.
:depends: - ``snapper`` http://snapper.io, available in most distros
'''

import salt.utils
from salt.exceptions import CommandExecutionError
import logging
import os
import time
import difflib
from pwd import getpwuid

try:
    import dbus
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False


def __virtual__():
    if not HAS_DBUS:
        return (False, 'The snapper module cannot be loaded:'
                ' missing python dbus module')
    if not salt.utils.which('snapper'):
        return (False, 'The snapper module cannot be loaded:'
                ' missing snapper')
    return 'snapper'


bus = dbus.SystemBus()
log = logging.getLogger(__name__)
snapper = dbus.Interface(bus.get_object('org.opensuse.Snapper',
                                        '/org/opensuse/Snapper'),
                         dbus_interface='org.opensuse.Snapper')


def _snapshot_to_data(snapshot):
    data = {}

    data['id'] = snapshot[0]
    data['type'] = ['single', 'pre', 'post'][snapshot[1]]
    if data['type'] == 'post':
        data['pre'] = snapshot[2]

    if snapshot[3] != -1:
        data['timestamp'] = snapshot[3]
    else:
        data['timestamp'] = time.time()

    data['user'] = getpwuid(snapshot[4])[0]
    data['description'] = snapshot[5]
    data['cleanup'] = snapshot[6]

    data['userdata'] = {}
    for k, v in snapshot[7].items():
        data['userdata'][k] = v

    return data


def _dbus_exception_to_reason(exc, args):
    '''
    Returns a error message from a snapper DBusException
    '''
    error = exc.get_dbus_name()
    if error == 'error.unknown_config':
        return "Unknown configuration '{0}'".format(args['config'])
    elif error == 'error.illegal_snapshot':
        return 'Invalid snapshot'
    else:
        return exc.get_dbus_name()


def list_snapshots(config='root'):
    '''
    List available snapshots

    CLI example:

    .. code-block:: bash

        salt '*' snapper.list_snapshots config=myconfig
    '''
    try:
        snapshots = snapper.ListSnapshots(config)
        return [_snapshot_to_data(s) for s in snapshots]
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while listing snapshots: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )


def get_snapshot(number=0, config='root'):
    '''
    Get detailed information about a given snapshot

    CLI example:

    .. code-block:: bash

        salt '*' snapper.get_snapshot 1
    '''
    try:
        snapshot = snapper.GetSnapshot(config, int(number))
        return _snapshot_to_data(snapshot)
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while retrieving snapshot: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )


def list_configs():
    '''
    List all available configs

    CLI example:

    .. code-block:: bash

        salt '*' snapper.list_configs
    '''
    try:
        configs = snapper.ListConfigs()
        return dict((config[0], config[2]) for config in configs)
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while listing configurations: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )


def _config_filter(x):
    if isinstance(x, bool):
        return 'yes' if x else 'no'
    return x


def set_config(name='root', **kwargs):
    '''
    Set configuration values

    CLI example:

    .. code-block:: bash

        salt '*' snapper.set_config SYNC_ACL=True

    Keys are case insensitive as they will be always uppercased to
    snapper convention. The above example is equivalent to:

    .. code-block:: bash
        salt '*' snapper.set_config sync_acl=True
    '''
    try:
        data = dict((k.upper(), _config_filter(v)) for k, v in
                    kwargs.iteritems() if not k.startswith('__'))
        snapper.SetConfig(name, data)
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while setting configuration {0}: {1}'
            .format(name, _dbus_exception_to_reason(exc, locals()))
        )
    return True


def _get_last_snapshot(config='root'):
    snapshot_list = sorted(list_snapshots(config), key=lambda x: x['id'])
    return snapshot_list[-1]


def _status_to_string(status):
    '''
    Converts a numeric dbus snapper status into a string
    '''
    return {2: 'deleted', 8: 'modified',
            1: 'created', 16: 'path'}.get(int(status), 'unknown')


def get_config(name='root'):
    '''
    Retrieves all values from a given configuration

    CLI example:

    .. code-block:: bash

      salt '*' snapper.get_config
    '''
    try:
        config = snapper.GetConfig(name)
        return config
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while retrieving configuration: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )


def create_snapshot(config='root', type='single', pre_number=None,
                    description=None, cleanup_algorithm='number', userdata={},
                    **kwargs):
    '''
    Creates an snapshot

    config
        Configuration name.
    type
        Specifies the type of the new snapshot. Possible values are
        single, pre and post.
    pre_number
        For post snapshots the number of the pre snapshot must be
        provided.
    description
        Description for the snapshot. If not given, the salt job will be used.
    cleanup_algorithm
        Set the cleanup algorithm for the snapshot.

        number
            Deletes old snapshots when a certain number of snapshots
            is reached.
        timeline
            Deletes old snapshots but keeps a number of hourly,
            daily, weekly, monthly and yearly snapshots.
        empty-pre-post
            Deletes pre/post snapshot pairs with empty diffs.
    userdata
        Set userdata for the snapshot (key-value pairs).

    Returns the number of the created snapshot.

    .. code-block:: bash
        salt '*' snapper.create_snapshot
    '''
    jid = kwargs.get('__pub_jid', None)
    if description is None and jid is not None:
        description = 'salt job {0}'.format(jid)

    if jid is not None:
        userdata['salt_jid'] = jid

    nr = None
    try:
        if type == 'single':
            nr = snapper.CreateSingleSnapshot(config, description,
                                              cleanup_algorithm, userdata)
        elif type == 'pre':
            nr = snapper.CreatePreSnapshot(config, description,
                                           cleanup_algorithm, userdata)
        elif type == 'post':
            if pre_number is None:
                raise CommandExecutionError(
                    "pre snapshot number 'pre_number' needs to be"
                    "specified for snapshots of the 'post' type")
            nr = snapper.CreatePostSnapshot(config, pre_number, description,
                                            cleanup_algorithm, userdata)
        else:
            raise CommandExecutionError(
                "Invalid snapshot type '{0}'", format(type))
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while listing changed files: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )
    return nr


def _get_num_interval(config, num_pre, num_post):
    post = int(num_post) if num_post else 0
    pre = int(num_pre) if num_pre else _get_last_snapshot(config)['id']
    return pre, post


def _is_text_file(filename):
    type_of_file = os.popen('file -bi {0}'.format(filename), 'r').read()
    return type_of_file.startswith('text')


def run(config='root', function=None, args=[], description=None,
        cleanup_algorithm='number', userdata={}, **kwargs):
    '''
    Runs a function from an execution module creating pre and post snapshots
    and associating the salt job id with those snapshots for easy undo and
    cleanup.

    .. code-block:: bash
        salt '*' snapper.run function=file.append \
          args='["/etc/motd", "some text"]

    This  would run append text to /etc/motd using the file.append
    module, and will create two snapshots, pre and post with the associated
    metadata. The jid will be available as salt_jid in the userdata of the
    snapshot.

    You can immediately see the changes
    '''
    pre_nr = __salt__['snapper.create_snapshot'](
        config=config,
        type='pre',
        description=description,
        cleanup_algorithm=cleanup_algorithm,
        userdata=userdata,
        **kwargs)

    ret = __salt__[function](*args, **kwargs)

    __salt__['snapper.create_snapshot'](
        config=config,
        type='post',
        pre_number=pre_nr,
        description=description,
        cleanup_algorithm=cleanup_algorithm,
        userdata=userdata,
        **kwargs)
    return ret


def status(config='root', num_pre=None, num_post=None):
        '''
    Returns a comparison between two snapshots

    config
        Configuration name.

    num_pre
        first snapshot ID to compare. Default is last snapshot

    num_post
        last snapshot ID to compare. Default is 0 (current state)

    CLI example:

    .. code-block:: bash

        salt '*' snapper.status
        salt '*' snapper.status num_pre=19 num_post=20
    '''
        try:
            pre, post = _get_num_interval(config, num_pre, num_post)
            snapper.CreateComparison(config, int(pre), int(post))
            files = snapper.GetFiles(config, int(pre), int(post))
            status = {}
            for file in files:
                status[file[0]] = file[1]
            return status
        except dbus.DBusException as exc:
            raise CommandExecutionError(
                'Error encountered while listing changed files: {0}'
                .format(_dbus_exception_to_reason(exc, locals()))
            )


def changed_files(config='root', num_pre=None, num_post=None):
    '''
    Returns the files changed between two snapshots

    config
        Configuration name.

    num_pre
        first snapshot ID to compare. Default is last snapshot

    num_post
        last snapshot ID to compare. Default is 0 (current state)

    CLI example:

    .. code-block:: bash

        salt '*' snapper.changed_files
        salt '*' snapper.changed_files num_pre=19 num_post=20
    '''
    return status(config, num_pre, num_post).keys()


def undo(config='root', files=None, num_pre=None, num_post=None):
    '''
    Undo all file changes that happened between num_pre and num_post, leaving
    the files into the state of num_pre.

    .. warning::
        If one of the files has changes after num_post, they will be overwriten
        The snapshots are used to determine the file list, but the current
        version of the files will be overwritten by the versions in num_pre.

        You to undo changes between num_pre and the current version of the
        files use num_post=0.
    '''
    pre, post = _get_num_interval(config, num_pre, num_post)

    changes = status(config, pre, post)
    changed = set(changes.keys())
    requested = set(files or changed)

    if not requested.issubset(changed):
        raise CommandExecutionError(
            'Given file list contains files that are not present'
            'in the changed filelist: {0}'.format(changed - requested))

    cmdret = __salt__['cmd.run']('snapper undochange {0}..{1} {2}'.format(
        pre, post, ' '.join(requested)))
    components = cmdret.split(' ')
    ret = {}
    for comp in components:
        key, val = comp.split(':')
        ret[key] = val
    return ret


def _get_jid_snapshots(jid, config='root'):
    jid_snapshots = filter(lambda x: x['userdata'].get("salt_jid", False) == jid,
                           list_snapshots(config))

    return (
        filter(lambda x: x['type'] == "pre", jid_snapshots)[0]['id'],
        filter(lambda x: x['type'] == "post", jid_snapshots)[0]['id']
    )


def undo_jid(jid, config='root'):
    '''
    Undo the changes applied by a salt job

    jid
        The job id to lookup

    config
        Configuration name.

    CLI example:

    .. code-block:: bash

        salt '*' snapper.undo_jid jid=20160607130930720112
    '''
    pre_snapshot, post_snapshot = _get_jid_snapshots(jid, config=config)
    return undo(config, num_pre=pre_snapshot, num_post=post_snapshot)


def diff(config='root', filename=None, num_pre=None, num_post=None):
    '''
    Returns the differences between two snapshots

    config
        Configuration name.

    filename
        if not provided the showing differences between snapshots for
        all "text" files

    num_pre
        first snapshot ID to compare. Default is last snapshot

    num_post
        last snapshot ID to compare. Default is 0 (current state)

    CLI example:

    .. code-block:: bash

        salt '*' snapper.diff
        salt '*' snapper.diff filename=/var/log/snapper.log num_pre=19 num_post=20
    '''
    try:
        pre, post = _get_num_interval(config, num_pre, num_post)

        files = changed_files(config, pre, post) if not filename else [filename]
        pre_mount = snapper.MountSnapshot(config, pre, False)
        post_mount = snapper.MountSnapshot(config, post, False) if post else ""

        files_diff = dict()
        for f in filter(lambda x: os.path.isfile(x), files):
            pre_file = pre_mount + f
            post_file = post_mount + f

            pre_file_content = open(pre_file).readlines() if os.path.isfile(pre_file) else []
            post_file_content = open(post_file).readlines() if os.path.isfile(post_file) else []

            if _is_text_file(post_file) or not post_file_content:
                files_diff[f] = ''.join(difflib.unified_diff(pre_file_content,
                                                             post_file_content,
                                                             fromfile=pre_file,
                                                             tofile=post_file))

        snapper.UmountSnapshot(config, pre, False)
        if post:
            snapper.UmountSnapshot(config, post, False)
        return files_diff
    except dbus.DBusException as exc:
        raise CommandExecutionError(
            'Error encountered while showing differences between snapshots: {0}'
            .format(_dbus_exception_to_reason(exc, locals()))
        )


def diff_jid(jid, config='root'):
    '''
    Returns the changes applied by a `jid`

    jid
        The job id to lookup

    config
        Configuration name.

    CLI example:

    .. code-block:: bash

        salt '*' snapper.diff_jid jid=20160607130930720112
    '''
    pre_snapshot, post_snapshot = _get_jid_snapshots(jid, config=config)
    return diff(config, num_pre=pre_snapshot, num_post=post_snapshot)
