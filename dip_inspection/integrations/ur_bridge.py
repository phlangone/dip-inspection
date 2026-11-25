"""Universal Robots RTDE register interface and robot telemetry."""
import rtde.rtde as rtde


class URBridge:

    # Commands: UR -> UNO Q
    """Exchange integer commands, responses, and destinations with the robot controller."""
    CMD_IDLE = 0
    CMD_OCR = 1
    CMD_SIDE_A = 2
    CMD_SIDE_B = 3
    CMD_PRESENCE = 4

    # Responses: UNO Q -> UR
    RESP_IDLE = 0
    RESP_OK = 1
    RESP_NOK = 2
    RESP_ERROR = 3

    # Destination: UNO Q -> UR
    DEST_NONE = 0

    def __init__(
        self,
        host,
        port=30004,
        frequency=10
    ):
        self.host = host
        self.port = port
        self.frequency = frequency

        self.con = None
        self.inputs = None
        self.state = None

    def connect(self):

        print(
            f"[RTDE] Conectando em "
            f"{self.host}:{self.port}..."
        )

        self.con = rtde.RTDE(
            self.host,
            self.port
        )

        self.con.connect()

        print(
            "[RTDE] Controlador:",
            self.con.get_controller_version()
        )

        # =====================================
        # Data received from the UR
        # =====================================

        output_names = [
            "timestamp",
            "output_int_register_24",
            "actual_q",
            "actual_TCP_pose",
            "safety_mode"
        ]

        output_types = [
            "DOUBLE",
            "INT32",
            "VECTOR6D",
            "VECTOR6D",
            "INT32"
        ]

        if not self.con.send_output_setup(
            output_names,
            output_types,
            frequency=self.frequency
        ):
            raise RuntimeError(
                "Falha ao configurar outputs RTDE."
            )

        # =====================================
        # Data sent to the UR
        #
        # register 24 = command response
        # register 25 = part destination
        # =====================================

        input_names = [
            "input_int_register_24",
            "input_int_register_25"
        ]

        input_types = [
            "INT32",
            "INT32"
        ]

        self.inputs = (
            self.con.send_input_setup(
                input_names,
                input_types
            )
        )

        if self.inputs is None:
            raise RuntimeError(
                "Falha ao configurar inputs RTDE."
            )

        # Initial state
        self.inputs.input_int_register_24 = (
            self.RESP_IDLE
        )

        self.inputs.input_int_register_25 = (
            self.DEST_NONE
        )

        # =====================================
        # Start RTDE synchronization
        # =====================================

        if not self.con.send_start():
            raise RuntimeError(
                "Falha ao iniciar RTDE."
            )

        # Send initial state
        self.con.send(
            self.inputs
        )

        print(
            "[RTDE] Comunicação iniciada."
        )

    def receive(self):

        self.state = self.con.receive()
        if self.state is None:
            raise ConnectionError("RTDE sem pacote válido.")
        return self.state

    # =========================================
    # Command received from the UR
    # =========================================

    def get_safety_mode(self):
        return getattr(self.state, "safety_mode", None)

    def get_command(self):

        if self.state is None:
            return self.CMD_IDLE

        return (
            self.state
            .output_int_register_24
        )

    # =========================================
    # Processing response
    # register 24
    # =========================================

    def send_response(
        self,
        response
    ):

        self.inputs.input_int_register_24 = (
            int(response)
        )

        return self.con.send(
            self.inputs
        )

    def clear_response(self):

        return self.send_response(
            self.RESP_IDLE
        )

    # =========================================
    # Part destination
    # register 25
    # =========================================

    def send_destination(
        self,
        destination
    ):

        self.inputs.input_int_register_25 = (
            int(destination)
        )

        return self.con.send(
            self.inputs
        )

    def clear_destination(self):

        return self.send_destination(
            self.DEST_NONE
        )

    # =========================================
    # Telemetry
    # =========================================

    def get_joints(self):

        if self.state is None:
            return None

        return list(
            self.state.actual_q
        )

    def get_tcp_pose(self):

        if self.state is None:
            return None

        return list(
            self.state.actual_TCP_pose
        )

    # =========================================
    # Shutdown
    # =========================================

    def close(self):

        if self.con is None:
            return

        try:
            self.clear_response()
            self.clear_destination()

        except Exception:
            pass

        try:
            self.con.send_pause()

        except Exception:
            pass

        self.con.disconnect()
        self.state = None
        self.inputs = None
        self.con = None

        print(
            "[RTDE] Desconectado."
        )