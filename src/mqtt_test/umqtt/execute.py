import ssl
import utime as time
from lib.rtc.rv3032 import rtc
from .lib.robust import MQTTClient


class MQTTControl():
    """
    A wrapper class for `umqtt.robust.MQTTClient` to simplify secure MQTT communication
    with AWS IoT Core using SSL/TLS certificates on MicroPython devices.

    This class handles the initialization of the MQTT client, certificate loading,
    connection management with retry mechanisms, and basic message publishing.
    It also provides a customizable callback for handling incoming MQTT messages.
    """
    def __init__(self, aws_iot_core_info: dict) -> None:
        """
        Initializes the MQTT client with the provided AWS IoT Core connection details.

        This constructor sets up the necessary SSL/TLS context using the client's
        certificate, private key, and the Amazon Root CA. It then configures
        the `umqtt.robust.MQTTClient` instance with the specified endpoint,
        client ID, port, and keep-alive interval.

        Args:
            aws_iot_core_info (dict): A dictionary containing all the necessary
                information to connect to AWS IoT Core. It must contain the
                following keys with their respective types and values:
                - "AWS_CERT_CRT_PATH" (str): Path to the client certificate file (.der)
                - "AWS_CERT_KEY_PATH" (str): Path to the client private key file (.der)
                - "AWS_CERT_CA_PATH" (str): Path to the CA certificate file (.der)
                - "AWS_IOT_ENDPOINT" (str): The AWS IoT Core endpoint URL.
                - "AWS_IOT_CLIENT_ID" (str): The unique client ID for the MQTT connection.
                - "AWS_MQTT_PORT" (int): The MQTT port, typically 8883 for SSL/TLS connections.
                - "MQTT_TOPIC" (str): The default MQTT topic to publish data to.

        Raises:
            Exception: If there is an error during the configuration of the SSL context
                       or the MQTT client.
        """

        self.AWS_CERT_CRT_PATH = aws_iot_core_info["AWS_CERT_CRT_PATH"]
        self.AWS_CERT_KEY_PATH = aws_iot_core_info["AWS_CERT_KEY_PATH"]
        self.AWS_CERT_CA_PATH = aws_iot_core_info["AWS_CERT_CA_PATH"]
        self.AWS_IOT_ENDPOINT = aws_iot_core_info["AWS_IOT_ENDPOINT"]
        self.AWS_IOT_CLIENT_ID = aws_iot_core_info["AWS_IOT_CLIENT_ID"]
        self.AWS_MQTT_PORT = aws_iot_core_info["AWS_MQTT_PORT"]
        self.MQTT_TOPIC = aws_iot_core_info["MQTT_TOPIC"]

        print("Configuring MQTT client...")

        try:
            # Lê os certificados dos arquivos PEM como bytes
            with open(self.AWS_CERT_KEY_PATH, 'rb') as f:
                key_data = f.read()
            with open(self.AWS_CERT_CRT_PATH, 'rb') as f:
                cert_data = f.read()
            
            # No MicroPython para Pico W, ssl.wrap_socket aceita key e cert como bytes
            ssl_params = {
                'key': key_data,
                'cert': cert_data,
                'server_hostname': self.AWS_IOT_ENDPOINT,
            }

            # Initialize the MQTTClient with AWS IoT Core specific parameters.
            # keepalive: The maximum period in seconds between communications with the broker.
            #            If no communication occurs, the client sends a PINGREQ.
            self.mqtt_client = MQTTClient(
                client_id=self.AWS_IOT_CLIENT_ID,
                server=self.AWS_IOT_ENDPOINT,
                port=self.AWS_MQTT_PORT,
                keepalive=5000,
                ssl=True,
                ssl_params=ssl_params
            )

            print("Successfully configured MQTT client...")
        except Exception as e:
            print(f"Error configuring MQTT client: {e}")
            raise

    def callback_mqtt(self, topic: bytes, msg: bytes) -> None:
        """
        Callback function to handle incoming MQTT messages.

        This method is automatically invoked by the `umqtt.robust.MQTTClient`
        whenever a message is received on a subscribed topic. It decodes the
        topic and message payload from bytes to UTF-8 strings and prints them
        to the console.

        Users can extend or override this method in a subclass to implement
        custom logic for processing incoming messages, such as parsing JSON,
        triggering device actions, or updating internal state.

        Args:
            topic (bytes): The raw topic of the received MQTT message as bytes.
            msg (bytes): The raw payload of the received MQTT message as bytes.
        """
        topic_str = topic.decode('utf-8')
        payload_str = msg.decode('utf-8')
        print("--- New MQTT Message Received ---")
        print(f"Topic: {topic_str}")
        print(f"Payload: {payload_str}")
        print("-------------------------------------")

    def connect(self) -> dict:
        """
        Establishes a secure connection to the AWS IoT Core MQTT broker.

        This method sets the `callback_mqtt` function as the message handler
        for incoming messages and attempts to connect to the MQTT broker.
        It includes a retry mechanism (up to 4 attempts) to handle transient
        connection issues, improving the robustness of the connection.

        Returns:
            dict: A dictionary indicating the connection status and any errors.
                - "success" (bool): `True` if the connection was successful,
                  `False` otherwise.
                - "error" (str, optional): An error message if the connection failed.
                  Present only when `success` is `False`.
                - "timestamp" (int): Unix timestamp (in seconds) of when the
                  connection attempt result was recorded.
        """
        print("Connecting to AWS IoT Core...")
        try:
            self.mqtt_client.set_callback(self.callback_mqtt)
            # Attempt to connect. If it fails (returns non-zero), enter retry loop.
            if self.mqtt_client.connect() != 0:
                print("Connection failed, retrying...")
                for trys in range(4):
                    print(f"Retrying connection ({trys+1}/4)...")
                    # Attempt connection again. If successful (returns 0), break loop.
                    if self.mqtt_client.connect() == 0:
                        break
                    # If this is the last retry and still no connection, return failure.
                    if trys == 3: # 0-indexed, so 3 is the 4th attempt
                        print("Failed to connect after 4 attempts.")
                        return {"success": False, "error": "Connection failed after retries.", "timestamp": rtc().get_unix_time()}
                    
            print(f"Connected as Device {self.AWS_IOT_CLIENT_ID}!")
            return {"success": True, "timestamp": rtc().get_unix_time()}
        except Exception as e:
            print(f"Error connecting: {e}")
            return {"success": False, "error": str(e), "timestamp": rtc().get_unix_time()}

    def publish(self, payload: bytes) -> dict:
        """
        Publishes a binary payload to the MQTT topic configured during initialization.

        The topic (`self.MQTT_TOPIC`) is encoded to bytes before publishing.

        Args:
            payload (bytes): The binary data to be published to the MQTT topic.

        Returns:
            dict: A dictionary indicating the publishing status and any errors.
                - "success" (bool): `True` if the message was published successfully,
                  `False` otherwise.
                - "error" (str, optional): An error message if publishing failed.
                  Present only when `success` is `False`.
                - "timestamp" (int): Unix timestamp (in seconds) of when the
                  publishing attempt result was recorded.
        """
        try:
            # Encode the MQTT topic to bytes as required by the publish method.
            self.mqtt_client.publish(self.MQTT_TOPIC.encode(), payload)
            return {"success": True, "timestamp": rtc().get_unix_time()}
        
        except Exception as e:
            print(f"Error publishing to topic {self.MQTT_TOPIC}: {e}")
            return {"success": False, "error": str(e), "timestamp": rtc().get_unix_time()}
    