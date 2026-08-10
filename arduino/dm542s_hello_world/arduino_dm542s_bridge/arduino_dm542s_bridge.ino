// UNO R4 Minima serial bridge for a DM542S common-cathode test rig.
// D3/D4 drive PUL+/DIR+; PUL- and DIR- connect to Arduino GND.
//
// Commands:
//   PING          serial + LED check, replies "PONG ..."
//   BLINK10       blink the onboard LED ten times
//   FWD           fixed 100-pulse move (original hello-world test)
//   CYCLE         fixed 200 forward / pause / 200 back (original hello-world test)
//   MOVE <steps>  signed step count; positive = forward, negative = backward
//   STATUS        report whether a MOVE is idle or running
//   STOP          abort an in-progress MOVE
//
// SAFETY: STOP is a software abort only. It cannot help if the Arduino hangs,
// the USB cable falls out, or the sketch crashes. It is NOT an emergency stop
// and is NOT a substitute for switching off the DM542S 24 V supply.
//
// PING/BLINK10/FWD/CYCLE keep their original blocking implementations and their
// exact original reply strings, so the existing hello-world scripts and tests
// are unaffected. Only MOVE uses the interruptible state machine below.

#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

const uint8_t STEP_PIN = 3;
const uint8_t DIR_PIN = 4;
const uint8_t LED_PIN = LED_BUILTIN;

const unsigned long BAUD_RATE = 115200;
const unsigned int HALF_PULSE_US = 5000;  // 5 ms low + 5 ms high = about 100 pps
const unsigned int DIR_SETUP_MS = 10;
const unsigned int CYCLE_PAUSE_MS = 2000;

// Conservative firmware ceiling for one MOVE command. At 800 pulses/revolution
// this is a little over six revolutions, and at about 100 pps it takes roughly
// 50 seconds. There are no limit switches, so keep this small.
const long MAX_MOVE_STEPS = 5000;

const size_t COMMAND_BUFFER_SIZE = 24;
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;
bool discardingLongCommand = false;

// ---------------------------------------------------------------------------
// Interruptible MOVE state machine
// ---------------------------------------------------------------------------
// Instead of busy-waiting inside a pulse loop, MOVE records what it still has
// to do and emits one pulse edge each time loop() notices the half-period has
// elapsed. loop() therefore keeps reading serial during a move, which is what
// lets STOP and STATUS be answered while the motor is turning.

bool moveActive = false;
long moveStepsRequested = 0;   // signed: negative means backward
long moveStepsCompleted = 0;   // magnitude only, always counts up
bool movePulseIsHigh = false;  // current STEP_PIN level within a pulse
unsigned long moveNextEdgeMicros = 0;


void setDirection(bool firstDirection) {
  // Either physical direction may be called "first"; motor wiring decides it.
  digitalWrite(DIR_PIN, firstDirection ? LOW : HIGH);
  delay(DIR_SETUP_MS);
}


void sendPulses(unsigned int count) {
  for (unsigned int pulse = 0; pulse < count; ++pulse) {
    digitalWrite(STEP_PIN, HIGH);  // active: source current through PUL input
    delayMicroseconds(HALF_PULSE_US);
    digitalWrite(STEP_PIN, LOW);   // idle
    delayMicroseconds(HALF_PULSE_US);
  }
}


void blinkLed(uint8_t count, unsigned int intervalMs) {
  for (uint8_t blink = 0; blink < count; ++blink) {
    digitalWrite(LED_PIN, HIGH);
    delay(intervalMs);
    digitalWrite(LED_PIN, LOW);
    delay(intervalMs);
  }
}


long signedStepsCompleted() {
  // Report progress with the same sign the operator asked for.
  return (moveStepsRequested < 0) ? -moveStepsCompleted : moveStepsCompleted;
}


void startMove(long signedSteps) {
  moveStepsRequested = signedSteps;
  moveStepsCompleted = 0;
  movePulseIsHigh = false;
  digitalWrite(STEP_PIN, LOW);

  // Positive steps are defined as forward, matching setDirection(true).
  setDirection(signedSteps > 0);

  moveNextEdgeMicros = micros();  // first edge may happen immediately
  moveActive = true;

  Serial.print("START MOVE ");
  Serial.println(signedSteps);
}


void finishMove() {
  digitalWrite(STEP_PIN, LOW);
  moveActive = false;

  Serial.print("DONE MOVE ");
  Serial.println(moveStepsRequested);
}


void abortMove() {
  // Leave STEP idle mid-move. The driver simply stops receiving pulses; it is
  // still energised and still holding torque.
  digitalWrite(STEP_PIN, LOW);
  moveActive = false;
  movePulseIsHigh = false;

  Serial.print("STOPPED MOVE ");
  Serial.print(signedStepsCompleted());
  Serial.print(" OF ");
  Serial.println(moveStepsRequested);
}


void serviceMove() {
  if (!moveActive) {
    return;
  }

  // Signed subtraction so the comparison survives the micros() rollover that
  // happens about every 71 minutes.
  const unsigned long now = micros();
  if (static_cast<long>(now - moveNextEdgeMicros) < 0) {
    return;
  }

  if (!movePulseIsHigh) {
    digitalWrite(STEP_PIN, HIGH);  // active: source current through PUL input
    movePulseIsHigh = true;
  } else {
    digitalWrite(STEP_PIN, LOW);   // idle; one complete pulse has been sent
    movePulseIsHigh = false;
    moveStepsCompleted += 1;
  }

  // Schedule from "now" rather than accumulating. If loop() is ever delayed the
  // pulse train simply stretches; it never bursts to catch up.
  moveNextEdgeMicros = now + HALF_PULSE_US;

  const long magnitude = (moveStepsRequested < 0) ? -moveStepsRequested
                                                  : moveStepsRequested;
  if (!movePulseIsHigh && moveStepsCompleted >= magnitude) {
    finishMove();
  }
}


void reportStatus() {
  if (!moveActive) {
    Serial.println("STATUS IDLE");
    return;
  }

  Serial.print("STATUS MOVING ");
  Serial.print(signedStepsCompleted());
  Serial.print(" OF ");
  Serial.println(moveStepsRequested);
}


bool parseSignedSteps(const char *argument, long *outSteps) {
  // Accept only a complete, plain signed integer such as "200" or "-200".
  // Rejected: "1.5", "12x", "", "1 2", "200 " (trailing characters), and any
  // value too large for a long. strtol does skip leading spaces and accepts a
  // leading '+', so " +200" parses as 200.
  if (argument[0] == '\0') {
    return false;
  }

  errno = 0;
  char *end = NULL;
  const long value = strtol(argument, &end, 10);
  if (end == argument || *end != '\0') {
    return false;
  }
  // Without this, "MOVE -99999999999" saturates to LONG_MIN, and negating
  // LONG_MIN is signed overflow: the magnitude check below would be bypassed.
  if (errno == ERANGE || value == LONG_MIN || value == LONG_MAX) {
    return false;
  }

  *outSteps = value;
  return true;
}


void executeMoveCommand(const char *argument) {
  long requestedSteps = 0;
  if (!parseSignedSteps(argument, &requestedSteps)) {
    Serial.print("ERROR move needs one whole signed step count, received: ");
    Serial.println(argument);
    return;
  }

  if (requestedSteps == 0) {
    Serial.println("ERROR move step count must not be zero");
    return;
  }

  // Compare against signed bounds directly. Never negate first: negating the
  // most negative long is undefined behaviour and would slip past the check.
  if (requestedSteps < -MAX_MOVE_STEPS || requestedSteps > MAX_MOVE_STEPS) {
    Serial.print("ERROR move step count exceeds firmware maximum ");
    Serial.println(MAX_MOVE_STEPS);
    return;
  }

  startMove(requestedSteps);
}


void executeCommand(const char *command) {
  // STATUS and STOP are the only commands answered while the motor is running.
  if (strcmp(command, "STATUS") == 0) {
    reportStatus();
    return;
  }

  if (strcmp(command, "STOP") == 0) {
    if (moveActive) {
      abortMove();
    } else {
      Serial.println("STOPPED IDLE");
    }
    return;
  }

  if (moveActive) {
    Serial.print("ERROR controller busy, move in progress: ");
    Serial.println(command);
    return;
  }

  if (strcmp(command, "PING") == 0) {
    blinkLed(3, 120);
    Serial.println("PONG Arduino serial and LED test passed");
    return;
  }

  if (strcmp(command, "BLINK10") == 0) {
    Serial.println("START LED BLINK 10");
    blinkLed(10, 100);
    Serial.println("DONE LED BLINK 10");
    return;
  }

  if (strcmp(command, "FWD") == 0) {
    Serial.println("START FWD 100");
    setDirection(true);
    sendPulses(100);
    Serial.println("DONE FWD 100");
    return;
  }

  if (strcmp(command, "CYCLE") == 0) {
    Serial.println("START CYCLE");
    setDirection(true);
    sendPulses(200);
    delay(CYCLE_PAUSE_MS);
    setDirection(false);
    sendPulses(200);
    Serial.println("DONE CYCLE");
    return;
  }

  if (strncmp(command, "MOVE ", 5) == 0) {
    executeMoveCommand(command + 5);
    return;
  }

  if (strcmp(command, "MOVE") == 0) {
    Serial.println("ERROR move needs a signed step count, for example: MOVE 200");
    return;
  }

  Serial.print("ERROR unknown command: ");
  Serial.println(command);
}


void readSerialByte() {
  // Exactly one byte per loop() pass, so serviceMove() keeps its pulse timing
  // even while a long command line is arriving.
  if (Serial.available() <= 0) {
    return;
  }

  const char incoming = static_cast<char>(Serial.read());

  if (incoming == '\r') {
    return;
  }

  if (incoming == '\n') {
    if (discardingLongCommand) {
      Serial.println("ERROR command too long");
    } else if (commandLength > 0) {
      commandBuffer[commandLength] = '\0';
      executeCommand(commandBuffer);
    }
    commandLength = 0;
    discardingLongCommand = false;
    return;
  }

  if (discardingLongCommand) {
    return;
  }

  if (commandLength < COMMAND_BUFFER_SIZE - 1) {
    commandBuffer[commandLength++] = incoming;
  } else {
    commandLength = 0;
    discardingLongCommand = true;
  }
}


void setup() {
  // Establish inactive signal levels before enabling the output drivers.
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.begin(BAUD_RATE);
  Serial.println("READY UNO_R4_DM542S");
}


void loop() {
  readSerialByte();
  serviceMove();
}
