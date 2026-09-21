// Minimal USB-serial smoke-test firmware for an Arduino UNO R4 Minima.
//
// This sketch intentionally configures no motor, STEP, DIR, ENABLE, or limit
// switch pins. It only answers ASCII commands over the USB serial connection.
// Uploading this sketch replaces any firmware currently loaded on the board.

const unsigned long BAUD_RATE = 115200;
const size_t COMMAND_CAPACITY = 64;

char commandBuffer[COMMAND_CAPACITY];
size_t commandLength = 0;

void handleCommand() {
  commandBuffer[commandLength] = '\0';

  if (strcmp(commandBuffer, "PING") == 0) {
    Serial.println("PONG ARDUINO_SMOKE_TEST");
  } else if (strcmp(commandBuffer, "INFO") == 0) {
    Serial.println("INFO device=arduino_smoke_test board=uno_r4_minima baud=115200 motion=false");
  } else if (commandLength > 0) {
    Serial.print("ERROR UNKNOWN_COMMAND ");
    Serial.println(commandBuffer);
  }

  commandLength = 0;
}

void setup() {
  Serial.begin(BAUD_RATE);
  unsigned long deadline = millis() + 3000;
  while (!Serial && millis() < deadline) {
    // Bounded wait for the native USB serial connection.
  }
  Serial.println("READY ARDUINO_SMOKE_TEST");
}

void loop() {
  while (Serial.available() > 0) {
    char incoming = static_cast<char>(Serial.read());
    if (incoming == '\n') {
      handleCommand();
    } else if (incoming != '\r') {
      if (commandLength < COMMAND_CAPACITY - 1) {
        commandBuffer[commandLength++] = incoming;
      } else {
        commandLength = 0;
        Serial.println("ERROR COMMAND_TOO_LONG");
      }
    }
  }
}
