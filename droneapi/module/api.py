import os
import time
import threading
import traceback
import logging
from pymavlink import mavutil
from pymavlink.dialects.v10.ardupilotmega import *
from MAVProxy.modules.lib import mp_module
from droneapi.lib import APIConnection, Vehicle, VehicleMode, Location, \
    Attitude, GPSInfo, Parameters, CommandSequence, APIException

# Enable logging here (until this code can be moved into mavproxy)
logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)

class MPParameters(Parameters):
    """
    See Parameters baseclass for documentation.

    FIXME - properly publish change notification
    """

    def __init__(self, module):
        self.__module = module

    def __getitem__(self, name):
        self.wait_valid()
        return self.__module.mav_param[name]

    def __setitem__(self, name, value):
        self.wait_valid()
        self.__module.mpstate.functions.param_set(name, value)

    def wait_valid(self):
        '''Block the calling thread until parameters have been downloaded'''
        # FIXME this is a super crufty spin-wait, also we should give the user the option of specifying a timeout
        pstate = self.__param.pstate
        while (pstate.mav_param_count == 0 or len(pstate.mav_param_set) != pstate.mav_param_count) and not self.__module.api.exit:
            time.sleep(0.200)

    @property
    def __param(self):
        return self.__module.module('param')

class MPCommandSequence(CommandSequence):
    """
    See CommandSequence baseclass for documentation.
    """

    def __init__(self, module):
        self.__module = module

    def download(self):
        '''Download all waypoints from the vehicle'''
        self.wait_valid()
        self.__wp.fetch()
        # BIG FIXME - wait for full wpt download before allowing any of the accessors to work

    def wait_valid(self):
        '''Block the calling thread until waypoints have been downloaded'''
        # FIXME this is a super crufty spin-wait, also we should give the user the option of specifying a timeout
        while (self.__wp.wp_op is not None) and not self.__module.api.exit:
            time.sleep(0.200)

    def takeoff(self, alt=None):
        if alt is not None:
            altitude = float(alt)
            self.__module.master.mav.command_long_send(self.__module.target_system,
                                                    self.__module.target_component,
                                                    mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                                                    0, 0, 0, 0, 0, 0, 0,
                                                    altitude)

    def goto(self, l):
        if l.is_relative:
            frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT
        else:
            frame = mavutil.mavlink.MAV_FRAME_GLOBAL
        self.__module.master.mav.mission_item_send(self.__module.target_system,
                                               self.__module.target_component,
                                               0,
                                               frame,
                                               mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                                               2, 0, 0, 0, 0, 0,
                                               l.lat, l.lon, l.alt)

    def clear(self):
        '''Clears the command list'''
        self.wait_valid()
        self.__wp.wploader.clear()
        self.__module.vehicle.wpts_dirty = True

    def add(self, cmd):
        '''Add a new command at the end of the command list'''
        self.wait_valid()
        self.__module.fix_targets(cmd)
        self.__wp.wploader.add(cmd, comment = 'Added by DroneAPI')
        self.__module.vehicle.wpts_dirty = True

    @property
    def __wp(self):
        return self.__module.module('wp')

    @property
    def count(self):
        return self.__wp.wploader.count()

    @property
    def next(self):
        """
        Currently active waypoint number

        (implementation provided by subclass)
        """
        return self.__module.last_waypoint

    @next.setter
    def next(self, index):
        self.__module.master.waypoint_set_current_send(index)

    def __getitem__(self, index):
        return self.__wp.wploader.wp(index)

    def __setitem__(self, index, value):
        self.__wp.wploader.set(value, index)
        self.__module.vehicle.wpts_dirty = True

class MPVehicle(Vehicle):
    def __init__(self, module):
        super(Vehicle,self).__init__()
        self.__module = module
        self._parameters = MPParameters(module)
        self._waypoints = None
        self.wpts_dirty = False

    def flush(self):
        if self.wpts_dirty:
            self.__module.module('wp').send_all_waypoints()
            self.wpts_dirty = False

    #
    # Private sugar methods
    #

    @property
    def __master(self):
        return self.__module.master

    @property
    def __mode_mapping(self):
        return self.__master.mode_mapping()

    #
    # Operations to support the standard API (FIXME - possibly/probably this
    # will move into a private dict of getter/setter tuples (invisible to the API consumer).
    #

    @property
    def mode(self):
        self.wait_init() # We must know vehicle type before this operation can work
        return self.__get_mode()

    def __get_mode(self):
        """Private method to read current vehicle mode without polling"""
        return VehicleMode(self.__module.status.flightmode)

    @mode.setter
    def mode(self, v):
        self.wait_init() # We must know vehicle type before this operation can work
        self.__master.set_mode(self.__mode_mapping[v.name])

    @property
    def location(self):
        return Location(self.__module.lat, self.__module.lon, self.__module.alt, is_relative=False, abs_alt=self.__module.abs_alt)

    @property
    def velocity(self):
        return [ self.__module.vx, self.__module.vy, self.__module.vz ]

    @property
    def attitude(self):
        return Attitude(self.__module.pitch, self.__module.yaw, self.__module.roll)

    @property
    def gps_0(self):
        return GPSInfo(self.__module.eph, self.__module.epv, self.__module.fix_type, self.__module.satellites_visible)

    @property
    def armed(self):
        return self.__module.mpstate.status.armed

    @armed.setter
    def armed(self, value):
        if value:
            self.__master.arducopter_arm()
        else:
            self.__master.arducopter_disarm()

    @property
    def system_status(self):
        return self.__module.system_status

    @property
    def groundspeed(self):
        return self.__module.groundspeed

    @property
    def airspeed(self):
        return self.__module.airspeed

    @property
    def mount_status(self):
        return [ self.__module.mount_pitch, self.__module.mount_yaw, self.__module.mount_roll ]

    @property
    def gopro_state(self):
        return [ self.__module.gopro_status, self.__module.gopro_capture_mode, self.__module.gopro_flags ]

    @property
    def gopro_get_response(self):
        return self.__module.gopro_get_response

    @property
    def gopro_set_response(self):
        return self.__module.gopro_set_response

    @property
    def ekf_ok(self):
        return self.__module.ekf_ok

    @property
    def channel_override(self):
        overrides = self.__rc.override
        # Only return entries that have a non zero override
        return dict((str(num + 1), overrides[num]) for num in range(8) if overrides[num] != 0)

    @channel_override.setter
    def channel_override(self, newch):
        overrides = self.__rc.override
        for k, v in newch.iteritems():
            overrides[int(k) - 1] = v
        self.__rc.set_override(overrides)

    @property
    def channel_readback(self):
        return self.__module.rc_readback

    @property
    def camera_trigger_msg(self):
        return self.__module.camera_trigger_msg

    @camera_trigger_msg.setter
    def camera_trigger_msg(self, msg):
        self.__module.camera_trigger_msg = msg

    @property
    def __rc(self):
        return self.__module.module('rc')

    @property
    def commands(self):
        """
        The (editable) waypoints for this vehicle.
        """
        if(self._waypoints is None):  # We create the wpts lazily (because this will start a fetch)
            self._waypoints = MPCommandSequence(self.__module)
        return self._waypoints

    def send_mavlink(self, message, fixTargets=True):
        if fixTargets:
            self.__module.fix_targets(message)
        self.__module.master.mav.send(message)

    @property
    def message_factory(self):
        """
        Returns an object that can be used to create 'raw' mavlink messages that are appropriate for this vehicle.
        These message types are defined in the central Mavlink github repository.  For example, a Pixhawk understands
        the following messages: (from https://github.com/mavlink/mavlink/blob/master/message_definitions/v1.0/pixhawk.xml).

          <message id="153" name="IMAGE_TRIGGER_CONTROL">
               <field type="uint8_t" name="enable">0 to disable, 1 to enable</field>
          </message>

        The name of the factory method will always be the lower case version of the message name with _encode appended.
        Each field in the xml message definition must be listed as arguments to this factory method.  So for this example
        message, the call would be:

        msg = vehicle.message_factory.image_trigger_control_encode(True)
        vehicle.send_mavlink(msg)
        """
        return self.__module.master.mav

    def wait_init(self):
        """Wait for the vehicle to exit the initializing step"""
        timeout = 30
        pollinterval = 0.2
        for i in range(0, int(timeout / pollinterval)):
            # Don't let the user try to fly while the board is still booting
            mode = self.__get_mode().name
            # print "mode is", mode
            if mode != "INITIALISING" and mode != "MAV":
                return

            time.sleep(pollinterval)
        raise APIException("Vehicle did not complete initialization")


class MPAPIConnection(APIConnection):
    """
    A small private version of the APIConnection class

    In Mavproxy you probably just want to call get_vehicles
    """
    def __init__(self, module):
        self.__vehicle = MPVehicle(module)

    def get_vehicles(self, query=None):
        return [ self.__vehicle ]

class APIThread(threading.Thread):
    def __init__(self, module, fn, description):
        super(APIThread, self).__init__()
        self.module = module
        self.description = description
        self.exit = False  # Python has no standard way to kill threads, this allows
        self.fn = fn
        self.thread_num = module.next_thread_num
        module.next_thread_num = module.next_thread_num + 1
        self.daemon = True  # For now I think it is okay to let mavproxy exit if api clients are still running
        self.start()
        self.name = "APIThread-%s" % self.thread_num
        self.module.thread_add(self)

        # DroneAPI might generate many commands, which in turn generate ots of acks and status text, in the interest of speed we ignore processing those messages
        try:
            self.module.mpstate.rx_blacklist.add('COMMAND_ACK')
            self.module.mpstate.rx_blacklist.add('STATUSTEXT')
        except:
            pass # Silently work with old mavproxies

    def kill(self):
        """Ask the thread to exit.  The thread must check threading.current_thread().exit periodically"""
        print("Asking %s to exit..." % self.name)
        self.exit = True

    def run(self):
        try:
            self.fn()
            print("%s exiting..." % self.name)
        except Exception as e:
            print("Exception in %s: %s" % (self.name, str(e)))
            traceback.print_exc()

        try:
            self.module.mpstate.rx_blacklist.remove('COMMAND_ACK')
            self.module.mpstate.rx_blacklist.remove('STATUSTEXT')
        except:
            pass # Silently work with old mavproxies
        self.module.thread_remove(self)

    def __str__(self):
        return "%s: %s" % (self.thread_num, self.description)

class APIModule(mp_module.MPModule):
    def __init__(self, mpstate):
        super(APIModule, self).__init__(mpstate, "api")

        self.add_command('api', self.cmd_api, "API commands", [ "<list>", "<start> (FILENAME)", "<stop> [THREAD_NUM]" ])
        self.api = MPAPIConnection(self)
        self.vehicle = self.api.get_vehicles()[0]
        self.system_status = None
        self.lat = None
        self.lon = None
        self.alt = None

        self.vx = None
        self.vy = None
        self.vz = None

        self.airspeed = None
        self.groundspeed = None

        self.pitch = None
        self.yaw = None
        self.roll = None
        self.pitchspeed = None
        self.yawspeed = None
        self.rollspeed = None

        self.mount_pitch = None
        self.mount_yaw = None
        self.mount_roll = None

        self.gopro_status = mavutil.mavlink.GOPRO_HEARTBEAT_STATUS_DISCONNECTED
        self.gopro_capture_mode = 0
        self.gopro_flags = 0
        self.gopro_get_response = ()
        self.gopro_set_response = ()

        self.rc_readback = {}

        self.last_waypoint = 0

        self.eph = None
        self.epv = None
        self.satellites_visible = None
        self.fix_type = None  # FIXME support multiple GPSs per vehicle - possibly by using componentId
        self.ekf_ok = False

        self.camera_trigger_msg = None

        self.next_thread_num = 0  # Monotonically increasing
        self.threads = {}  # A map from int ID to thread object

        self.local_path = os.path.dirname(os.getcwd())
        print("DroneAPI loaded")

    def fix_targets(self, message):
        """Set correct target IDs for our vehicle"""
        settings = self.mpstate.settings
        if hasattr(message, 'target_system'):
            message.target_system = settings.target_system
        if hasattr(message, 'target_component'):
            message.target_component = settings.target_component

    def __on_change(self, *args):
        for a in args:
            self.vehicle.notify_observers(a)

    def unload(self):
        """We ask any api threads to exit"""
        for t in self.threads.values():
            t.kill()
        for t in self.threads.values():
            t.join(5)
            if t.is_alive():
                print("WARNING: Timed out waiting for %s to exit." % t)

    def mavlink_packet(self, m):
        typ = m.get_type()
        if typ == 'GLOBAL_POSITION_INT':
            (self.lat, self.lon) = (m.lat / 1.0e7, m.lon / 1.0e7)
            self.abs_alt = (m.alt or 0.0) / 1000.0
            (self.vx, self.vy, self.vz) = (m.vx / 100.0, m.vy / 100.0, m.vz / 100.0)
            self.__on_change('location', 'velocity')
        elif typ == 'GPS_RAW':
            pass # better to just use global position int
            # (self.lat, self.lon) = (m.lat, m.lon)
            # self.__on_change('location')
        elif typ == 'GPS_RAW_INT':
            # (self.lat, self.lon) = (m.lat / 1.0e7, m.lon / 1.0e7)
            self.eph = m.eph
            self.epv = m.epv
            self.satellites_visible = m.satellites_visible
            self.fix_type = m.fix_type
            self.__on_change('gps_0')
        elif typ == "VFR_HUD":
            self.heading = m.heading
            self.alt = m.alt
            self.airspeed = m.airspeed
            self.groundspeed = m.groundspeed
            self.__on_change('location', 'airspeed', 'groundspeed')
        elif typ == "ATTITUDE":
            self.pitch = m.pitch
            self.yaw = m.yaw
            self.roll = m.roll
            self.pitchspeed = m.pitchspeed
            self.yawspeed = m.yawspeed
            self.rollspeed = m.rollspeed
            self.__on_change('attitude')
        elif typ == "HEARTBEAT":
            self.system_status = m.system_status
            self.__on_change('mode', 'armed')
        elif typ in ["WAYPOINT_CURRENT", "MISSION_CURRENT"]:
            self.last_waypoint = m.seq
        elif typ == "RC_CHANNELS_RAW":
            def set(chnum, v):
                '''Private utility for handling rc channel messages'''
                # use port to allow ch nums greater than 8
                self.rc_readback[str(m.port * 8 + chnum)] = v

            set(1, m.chan1_raw)
            set(2, m.chan2_raw)
            set(3, m.chan3_raw)
            set(4, m.chan4_raw)
            set(5, m.chan5_raw)
            set(6, m.chan6_raw)
            set(7, m.chan7_raw)
            set(8, m.chan8_raw)
        elif typ == "MOUNT_STATUS":
            self.mount_pitch = m.pointing_a / 100
            self.mount_roll = m.pointing_b / 100
            self.mount_yaw = m.pointing_c / 100
            self.__on_change('mount')
        elif typ == "GOPRO_HEARTBEAT":
            self.gopro_status = m.status
            self.gopro_capture_mode = m.capture_mode
            self.gopro_flags = m.flags
            self.__on_change('gopro_state')
        elif typ == "GOPRO_GET_RESPONSE":
            self.gopro_get_response = (m.cmd_id, m.status, m.value)
            self.__on_change('gopro_get_response')
        elif typ == "GOPRO_SET_RESPONSE":
            self.gopro_set_response = (m.cmd_id, m.status)
            self.__on_change('gopro_set_response')
        elif typ == "EKF_STATUS_REPORT":
            # use same check that ArduCopter::system.pde::position_ok() is using
            if self.vehicle.armed:
                self.ekf_ok = ((m.flags&EKF_POS_HORIZ_ABS) > 0) and (m.flags&EKF_CONST_POS_MODE == 0)
            else:
                self.ekf_ok = ((m.flags&EKF_POS_HORIZ_ABS) > 0) or ((m.flags&EKF_PRED_POS_HORIZ_ABS) > 0)
            self.__on_change('ekf_ok')
        elif typ == "CAMERA_FEEDBACK":
            self.camera_trigger_msg = m
            self.__on_change('camera_trigger')

        if (self.vehicle is not None) and hasattr(self.vehicle, 'mavrx_callback'):
            self.vehicle.mavrx_callback(m)

    def thread_remove(self, t):
        if t.thread_num in self.threads.keys():
            del self.threads[t.thread_num]

    def thread_add(self, t):
        self.threads[t.thread_num] = t

    def cmd_list(self):
        print("API Threads:")
        for t in self.threads.values():
            print("  " + str(t))

    def cmd_kill(self, n):
        if self.threads[n].isAlive():
            self.threads[n].kill()

    def get_connection(self):
        return self.api

    def cmd_api(self, args):
        if len(args) < 1:
            print("usage: api <list|start|stop> [filename or threadnum]")
            return

        if args[0] == "list":
            self.cmd_list()
        elif args[0] == "stop":
            if len(args) > 2:
                print("usage: api stop [thread-num]")
                return
            elif len(args) > 1:
                self.cmd_kill(int(args[1]))
            elif len(self.threads) > 1:
                # Just kill the youngest
                self.cmd_kill(max(self.threads.keys))
        elif args[0] == "start":
            if len(args) < 2:
                print("usage: api start <filename> [arguments]")
                return

            g = {
                "local_connect" : self.get_connection,
                "local_path": os.path.dirname(os.path.abspath(args[1])), # The path to the executable script dir (so scripts can construct relative paths)
                "local_arguments": args[2:]
            }

            APIThread(self, lambda: execfile(args[1], g), args[1])
        else:
            print("Invalid api subcommand")

def init(mpstate):
    '''initialise module'''
    return APIModule(mpstate)
