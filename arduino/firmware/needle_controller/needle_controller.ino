/*
  Canonical runtime-configured needle controller 1.2.1 for Arduino UNO R4
  Minima. This is the only active sketch in the repository.

  Motion remains disabled after every reset until the host applies reviewed
  YAML commissioning values with CONFIG_IO, CONFIG_LIMITS, and CONFIG_APPLY.
*/

#include <Arduino.h>
#include <errno.h>
#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const char *DEVICE_NAME = "needle_controller";
const char *BOARD_NAME = "uno_r4_minima";
const char *FIRMWARE_VERSION = "1.2.1";
const char *DRIVER_MODEL = "DM542S";

bool runtimeMotionCommissioned = false;
bool runtimeLimitsCommissioned = false;
bool runtimeDriverSignalsInverted = false;
bool runtimeEnableActiveLow = true;
bool runtimeUpperLimitActiveHigh = true;
bool runtimeLowerLimitActiveHigh = true;
bool runtimeConfigured = false;
bool runtimeIoPolarityLocked = false;

#define MOTION_COMMISSIONED runtimeMotionCommissioned
#define LIMITS_COMMISSIONED runtimeLimitsCommissioned
#define DRIVER_SIGNALS_INVERTED runtimeDriverSignalsInverted
#define ENABLE_ACTIVE_LOW runtimeEnableActiveLow
#define UPPER_LIMIT_ACTIVE_HIGH runtimeUpperLimitActiveHigh
#define LOWER_LIMIT_ACTIVE_HIGH runtimeLowerLimitActiveHigh

// Validated bridge wiring. ENA is not connected; no limit switches exist.
const uint8_t STEP_PIN = 3;
const uint8_t DIR_PIN = 4;

const size_t INPUT_CAPACITY = 96;
const long MAX_COMMAND_STEPS = 200000L;
const uint8_t MAX_SERIAL_BYTES_PER_LOOP = 32;
const unsigned long USB_HOST_SETTLE_MS = 250UL;
// Preserve the proven bridge's 5 ms HIGH / 5 ms LOW pulse timing at top speed.
const unsigned long ABSOLUTE_MAX_SPEED_STEPS_S = 100UL;
const unsigned long ABSOLUTE_MAX_ACCELERATION_STEPS_S2 = 50000UL;
const unsigned long DEFAULT_UNLOADED_ACCELERATION_STEPS_S2 = 500UL;
// Set these three exact values only during axis commissioning. Test 2 can use
// the absolute pulse/step caps while the commanded axis position is unknown.
long runtimeMaximumTravelSteps = 0L;
unsigned long runtimeMaximumSpeedStepsS = 0UL;
unsigned long runtimeMaximumAccelerationStepsS2 = 0UL;
unsigned long runtimeHomeSpeedStepsS = 0UL;
#define COMMISSIONED_MAX_TRAVEL_STEPS runtimeMaximumTravelSteps
#define COMMISSIONED_MAX_SPEED_STEPS_S runtimeMaximumSpeedStepsS
#define COMMISSIONED_MAX_ACCELERATION_STEPS_S2 runtimeMaximumAccelerationStepsS2
#define COMMISSIONED_HOME_SPEED_STEPS_S runtimeHomeSpeedStepsS
const unsigned long MIN_STEP_INTERVAL_US = 10000UL;
const unsigned long STEP_HIGH_US = 5000UL;
const unsigned long COMMUNICATION_LOSS_MS = 3000UL;
const unsigned long ABSOLUTE_MOVEMENT_TIMEOUT_MS = 120000UL;
const unsigned long BLINK_HALF_PERIOD_MS = 120UL;
const unsigned long HOME_LIMIT_STABLE_MS = 10UL;

char inputBuffer[INPUT_CAPACITY];
size_t inputLength = 0;
bool discardingOversizedLine = false;
bool serialHostConnected = false;
bool readyAnnouncedForHost = false;
unsigned long serialHostConnectedAtMs = 0;

bool driverEnabled = false;
bool moving = false;
bool homing = false;
bool stepHigh = false;
bool homed = false;
bool positionKnown = false;
bool ledOn = false;
bool faultLatched = false;
char faultCode[32] = "NONE";

bool pendingMotionCommissioned = false;
bool pendingLimitsCommissioned = false;
bool pendingDriverSignalsInverted = false;
bool pendingEnableActiveLow = true;
bool pendingUpperLimitActiveHigh = true;
bool pendingLowerLimitActiveHigh = true;
long pendingMaximumTravelSteps = 0L;
unsigned long pendingMaximumSpeedStepsS = 0UL;
unsigned long pendingMaximumAccelerationStepsS2 = 0UL;
unsigned long pendingHomeSpeedStepsS = 0UL;
bool pendingIoReceived = false;
bool pendingLimitsReceived = false;

long commandedPositionSteps = 0;  // Command-derived only; no physical encoder exists.
long remainingSteps = 0;
int8_t motionDirection = 0;       // Positive = legacy bridge forward (DIR LOW).
unsigned long stepIntervalUs = 0;
float currentSpeedStepsS = 0.0f;
float targetSpeedStepsS = 0.0f;
float accelerationStepsS2 = 0.0f;
unsigned long lastStepEdgeUs = 0;
unsigned long movementStartedMs = 0;
unsigned long movementTimeoutMs = 0;
unsigned long lastCommunicationMs = 0;
long activeSequence = 0;
char activeCommand[16] = "";
bool homeLimitCandidate = false;
unsigned long homeLimitCandidateMs = 0;

bool blinkActive = false;
uint8_t blinkTransitionsRemaining = 0;
unsigned long lastBlinkTransitionMs = 0;
long blinkSequence = 0;

bool logicalOutput(bool asserted) {
  return DRIVER_SIGNALS_INVERTED ? !asserted : asserted;
}

void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_BUILTIN, on ? HIGH : LOW);
}

void setDriverEnabled(bool enabled) {
  // Protocol software arm only: there is no physical ENA connection.
  driverEnabled = enabled && MOTION_COMMISSIONED;
}

bool upperLimitActive() {
  return false; // No upper switch is wired.
}

bool lowerLimitActive() {
  return false; // No lower switch is wired.
}

void printAck(long sequence, const char *command) {
  Serial.print("ACK ");
  Serial.print(sequence);
  Serial.print(" ");
  Serial.println(command);
}

void printDone(long sequence, const char *detail) {
  Serial.print("DONE ");
  Serial.print(sequence);
  if (detail != NULL && detail[0] != '\0') {
    Serial.print(" ");
    Serial.print(detail);
  }
  Serial.println();
}

void printError(long sequence, const char *code) {
  Serial.print("ERR ");
  Serial.print(sequence > 0 ? sequence : 1);
  Serial.print(" ");
  Serial.println(code);
}

void printLimitEvent(const char *name, bool active) {
  Serial.print("EVENT ");
  Serial.print(name);
  Serial.print(" ");
  Serial.println(active ? "ACTIVE" : "INACTIVE");
}

void printReadyIdentity() {
  char line[96];
  snprintf(line, sizeof(line), "READY device=%s board=%s version=%s",
           DEVICE_NAME, BOARD_NAME, FIRMWARE_VERSION);
  Serial.println(line);
}

void serviceSerialConnection() {
  // UNO R4 Minima's USB Serial becomes true when the CDC host opens the port.
  // Allow the host's read endpoint to settle before the first USB write.
  // Announce once per connection, including when setup finished long ago.
  bool connected = (bool)Serial;
  if (connected && !serialHostConnected) {
    serialHostConnected = true;
    readyAnnouncedForHost = false;
    serialHostConnectedAtMs = millis();
    inputLength = 0;
    discardingOversizedLine = false;
  }
  if (connected && !readyAnnouncedForHost &&
      millis() - serialHostConnectedAtMs >= USB_HOST_SETTLE_MS) {
    printReadyIdentity();
    Serial.println("EVENT HOST_CONNECTED");
    readyAnnouncedForHost = true;
  } else if (!connected && serialHostConnected) {
    serialHostConnected = false;
    readyAnnouncedForHost = false;
    inputLength = 0;
    discardingOversizedLine = false;
    // No event can be delivered safely after the host has disconnected.
  }
}

void stopPulseGeneration(bool positionUncertain) {
  moving = false;
  homing = false;
  stepHigh = false;
  digitalWrite(STEP_PIN, logicalOutput(false) ? HIGH : LOW);
  remainingSteps = 0;
  motionDirection = 0;
  homeLimitCandidate = false;
  if (positionUncertain) {
    positionKnown = false;
  }
}

void latchFault(const char *code, bool positionUncertain) {
  stopPulseGeneration(positionUncertain);
  faultLatched = true;
  strncpy(faultCode, code, sizeof(faultCode) - 1);
  faultCode[sizeof(faultCode) - 1] = '\0';
  Serial.print("EVENT FAULT ");
  Serial.println(faultCode);
  if (activeSequence > 0) {
    printError(activeSequence, faultCode);
    activeSequence = 0;
    activeCommand[0] = '\0';
  }
}

bool parseLongExact(const char *text, long &value) {
  if (text == NULL || text[0] == '\0') return false;
  errno = 0;
  char *endPointer = NULL;
  value = strtol(text, &endPointer, 10);
  return errno != ERANGE && endPointer != text && *endPointer == '\0';
}

bool movementDirectionBlocked(int8_t direction) {
  return (direction < 0 && upperLimitActive()) ||
         (direction > 0 && lowerLimitActive());
}

void beginMovement(long sequence, const char *command, long steps, unsigned long speed, bool isHoming) {
  if (!runtimeConfigured) {
    printError(sequence, "CONFIG_REQUIRED");
    return;
  }
  if (!MOTION_COMMISSIONED) {
    printError(sequence, "MOTION_NOT_COMMISSIONED");
    return;
  }
  if (!driverEnabled) {
    printError(sequence, "DRIVER_DISABLED");
    return;
  }
  if (faultLatched) {
    printError(sequence, "FAULT_LATCHED");
    return;
  }
  if (moving) {
    printError(sequence, "BUSY");
    return;
  }
  if (upperLimitActive() && lowerLimitActive()) {
    activeSequence = sequence;
    latchFault("BOTH_LIMITS_ACTIVE", true);
    return;
  }
  if (steps == 0 || steps < -MAX_COMMAND_STEPS || steps > MAX_COMMAND_STEPS) {
    printError(sequence, "OUT_OF_RANGE");
    return;
  }
  int8_t direction = steps < 0 ? -1 : 1;
  // HOME may begin with the upper switch already active. In that one case,
  // serviceMovement qualifies the stable switch without producing a pulse.
  bool homingAtUpper = isHoming && direction < 0 && upperLimitActive();
  if (movementDirectionBlocked(direction) && !homingAtUpper) {
    printError(sequence, direction < 0 ? "LIMIT_UP_ACTIVE" : "LIMIT_DOWN_ACTIVE");
    return;
  }
  unsigned long count = (unsigned long)(steps < 0 ? -steps : steps);
  unsigned long commissionedSpeed = COMMISSIONED_MAX_SPEED_STEPS_S > 0
                                      ? COMMISSIONED_MAX_SPEED_STEPS_S
                                      : ABSOLUTE_MAX_SPEED_STEPS_S;
  if (speed == 0 || speed > commissionedSpeed || speed > ABSOLUTE_MAX_SPEED_STEPS_S) {
    printError(sequence, "OUT_OF_RANGE");
    return;
  }
  if (positionKnown && !isHoming) {
    long target = commandedPositionSteps + steps;
    if (COMMISSIONED_MAX_TRAVEL_STEPS <= 0 || target < 0 || target > COMMISSIONED_MAX_TRAVEL_STEPS) {
      printError(sequence, "TRAVEL_LIMIT");
      return;
    }
  }
  printAck(sequence, command);
  // The proven D3/D4 bridge uses LOW for positive/forward steps.
  digitalWrite(DIR_PIN, logicalOutput(direction < 0) ? HIGH : LOW);
  remainingSteps = (long)count;
  motionDirection = direction;
  targetSpeedStepsS = (float)speed;
  accelerationStepsS2 = (float)(
      COMMISSIONED_MAX_ACCELERATION_STEPS_S2 > 0
          ? COMMISSIONED_MAX_ACCELERATION_STEPS_S2
          : DEFAULT_UNLOADED_ACCELERATION_STEPS_S2);
  currentSpeedStepsS = min(targetSpeedStepsS, 50.0f);
  stepIntervalUs = max(MIN_STEP_INTERVAL_US, (unsigned long)(1000000.0f / currentSpeedStepsS));
  movementStartedMs = millis();
  // Include a conservative acceleration/deceleration allowance so the
  // firmware deadline cannot expire merely because a reviewed ramp is slow.
  unsigned long rampAllowanceMs =
      (2UL * speed * 1000UL) / max(1UL, (unsigned long)accelerationStepsS2);
  unsigned long estimatedMs = (count * 1000UL) / speed + rampAllowanceMs + 5000UL;
  movementTimeoutMs = min(ABSOLUTE_MOVEMENT_TIMEOUT_MS, max(estimatedMs, 5000UL));
  lastStepEdgeUs = micros();
  activeSequence = sequence;
  strncpy(activeCommand, command, sizeof(activeCommand) - 1);
  activeCommand[sizeof(activeCommand) - 1] = '\0';
  homing = isHoming;
  moving = true;
}

void finishMovement() {
  long sequence = activeSequence;
  stopPulseGeneration(false);
  activeSequence = 0;
  activeCommand[0] = '\0';
  Serial.print("DONE ");
  Serial.print(sequence);
  Serial.print(" position_steps=");
  Serial.print(commandedPositionSteps);
  Serial.print(" position_is_commanded_only=true position_known=");
  Serial.println(positionKnown ? "true" : "false");
}

void serviceMovement() {
  if (!moving) return;
  if (upperLimitActive() && lowerLimitActive()) {
    latchFault("BOTH_LIMITS_ACTIVE", true);
    return;
  }
  // On UNO R4's native USB serial, a dropped host connection makes Serial
  // false. Mere command silence is not treated as loss while a bounded move
  // is legitimately running.
  if (!Serial && millis() - lastCommunicationMs > COMMUNICATION_LOSS_MS) {
    latchFault("COMMUNICATION_LOSS", true);
    return;
  }
  if (millis() - movementStartedMs > movementTimeoutMs) {
    latchFault("MOTOR_TIMEOUT", true);
    return;
  }
  // HOME is the sole movement allowed to terminate normally at the upper
  // switch. Qualify the input for a short stable interval with STEP inactive;
  // a transient release resumes homing without declaring a false home.
  if (homing && motionDirection < 0) {
    if (upperLimitActive()) {
      if (!homeLimitCandidate) {
        homeLimitCandidate = true;
        homeLimitCandidateMs = millis();
        digitalWrite(STEP_PIN, logicalOutput(false) ? HIGH : LOW);
        stepHigh = false;
        printLimitEvent("LIMIT_UP", true);
      }
      if (millis() - homeLimitCandidateMs >= HOME_LIMIT_STABLE_MS) {
        commandedPositionSteps = 0;
        homed = true;
        positionKnown = true;
        finishMovement();
      }
      return;
    }
    homeLimitCandidate = false;
  }
  if (movementDirectionBlocked(motionDirection)) {
    printLimitEvent(motionDirection < 0 ? "LIMIT_UP" : "LIMIT_DOWN", true);
    latchFault(motionDirection < 0 ? "LIMIT_UP_ACTIVE" : "LIMIT_DOWN_ACTIVE", true);
    return;
  }

  unsigned long nowUs = micros();
  if (!stepHigh && nowUs - lastStepEdgeUs >= stepIntervalUs) {
    digitalWrite(STEP_PIN, logicalOutput(true) ? HIGH : LOW);
    stepHigh = true;
    lastStepEdgeUs = nowUs;
    return;
  }
  if (stepHigh && nowUs - lastStepEdgeUs >= STEP_HIGH_US) {
    digitalWrite(STEP_PIN, logicalOutput(false) ? HIGH : LOW);
    stepHigh = false;
    lastStepEdgeUs = nowUs;
    remainingSteps--;
    commandedPositionSteps += motionDirection; // Reset on MCU reboot; never an absolute reference.
    if (remainingSteps <= 0) {
      finishMovement();
      return;
    }
    // Bounded trapezoidal-style ramp. The acceleration is a commissioned
    // compile-time ceiling; commands can request speed but cannot raise it.
    float stoppingSteps = (currentSpeedStepsS * currentSpeedStepsS) /
                          (2.0f * accelerationStepsS2);
    float speedDelta = max(1.0f, accelerationStepsS2 / max(currentSpeedStepsS, 1.0f));
    if ((float)remainingSteps <= stoppingSteps) {
      currentSpeedStepsS = max(min(targetSpeedStepsS, 50.0f), currentSpeedStepsS - speedDelta);
    } else {
      currentSpeedStepsS = min(targetSpeedStepsS, currentSpeedStepsS + speedDelta);
    }
    stepIntervalUs = max(
        MIN_STEP_INTERVAL_US,
        (unsigned long)(1000000.0f / max(currentSpeedStepsS, 1.0f)));
  }
}

void serviceBlink() {
  if (!blinkActive) return;
  if (millis() - lastBlinkTransitionMs < BLINK_HALF_PERIOD_MS) return;
  setLed(!ledOn);
  lastBlinkTransitionMs = millis();
  if (blinkTransitionsRemaining > 0) blinkTransitionsRemaining--;
  if (blinkTransitionsRemaining == 0) {
    setLed(false);
    blinkActive = false;
    printDone(blinkSequence, "blinks=3 led=off");
    blinkSequence = 0;
  }
}

void printStatus(long sequence) {
  printAck(sequence, "STATUS");
  Serial.print("DONE ");
  Serial.print(sequence);
  Serial.print(" enabled="); Serial.print(driverEnabled ? "true" : "false");
  Serial.print(" enable_output_present=false");
  Serial.print(" moving="); Serial.print(moving ? "true" : "false");
  Serial.print(" led="); Serial.print(ledOn ? "on" : "off");
  Serial.print(" homed="); Serial.print(homed ? "true" : "false");
  Serial.print(" position_known="); Serial.print(positionKnown ? "true" : "false");
  Serial.print(" commanded_position_steps="); Serial.print(commandedPositionSteps);
  Serial.print(" position_is_commanded_only=true");
  Serial.print(" limit_up="); Serial.print(upperLimitActive() ? "true" : "false");
  Serial.print(" limit_down="); Serial.print(lowerLimitActive() ? "true" : "false");
  Serial.print(" physical_limits_present=false");
  Serial.print(" motion_commissioned="); Serial.print(MOTION_COMMISSIONED ? "true" : "false");
  Serial.print(" limits_commissioned="); Serial.print(LIMITS_COMMISSIONED ? "true" : "false");
  Serial.print(" signal_inverted="); Serial.print(DRIVER_SIGNALS_INVERTED ? "true" : "false");
  Serial.print(" enable_active_low="); Serial.print(ENABLE_ACTIVE_LOW ? "true" : "false");
  Serial.print(" upper_active_low="); Serial.print(UPPER_LIMIT_ACTIVE_HIGH ? "false" : "true");
  Serial.print(" lower_active_low="); Serial.print(LOWER_LIMIT_ACTIVE_HIGH ? "false" : "true");
  Serial.print(" driver_model="); Serial.print(DRIVER_MODEL);
  Serial.print(" maximum_travel_steps="); Serial.print(COMMISSIONED_MAX_TRAVEL_STEPS);
  Serial.print(" maximum_speed_steps_s="); Serial.print(COMMISSIONED_MAX_SPEED_STEPS_S);
  Serial.print(" maximum_acceleration_steps_s2="); Serial.print(COMMISSIONED_MAX_ACCELERATION_STEPS_S2);
  Serial.print(" home_speed_steps_s="); Serial.print(COMMISSIONED_HOME_SPEED_STEPS_S);
  Serial.print(" runtime_configurable=true");
  Serial.print(" runtime_configured="); Serial.print(runtimeConfigured ? "true" : "false");
  Serial.print(" fault="); Serial.println(faultCode);
}

void uppercase(char *text) {
  while (*text) {
    *text = (char)toupper((unsigned char)*text);
    text++;
  }
}

bool parseBinaryToken(const char *text, bool &value) {
  long parsed = 0;
  if (!parseLongExact(text, parsed) || (parsed != 0 && parsed != 1)) return false;
  value = parsed == 1;
  return true;
}

void handleConfigIo(long sequence, char **savePointer) {
  if (moving || driverEnabled) { printError(sequence, "CONFIG_STATE"); return; }
  char *tokens[6];
  for (uint8_t index = 0; index < 6; index++) {
    tokens[index] = strtok_r(NULL, " ", savePointer);
    if (tokens[index] == NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
  }
  if (strtok_r(NULL, " ", savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
  bool motion, limits, signalInverted, enableActiveLow, upperActiveLow, lowerActiveLow;
  if (!parseBinaryToken(tokens[0], motion) ||
      !parseBinaryToken(tokens[1], limits) ||
      !parseBinaryToken(tokens[2], signalInverted) ||
      !parseBinaryToken(tokens[3], enableActiveLow) ||
      !parseBinaryToken(tokens[4], upperActiveLow) ||
      !parseBinaryToken(tokens[5], lowerActiveLow)) {
    printError(sequence, "MALFORMED_COMMAND");
    return;
  }
  if (limits) { printError(sequence, "PHYSICAL_LIMITS_UNAVAILABLE"); return; }
  pendingMotionCommissioned = motion;
  pendingLimitsCommissioned = limits;
  pendingDriverSignalsInverted = signalInverted;
  pendingEnableActiveLow = enableActiveLow;
  pendingUpperLimitActiveHigh = !upperActiveLow;
  pendingLowerLimitActiveHigh = !lowerActiveLow;
  pendingIoReceived = true;
  printAck(sequence, "CONFIG_IO");
  printDone(sequence, "staged=true");
}

void handleConfigLimits(long sequence, char **savePointer) {
  if (moving || driverEnabled) { printError(sequence, "CONFIG_STATE"); return; }
  char *tokens[4];
  long values[4];
  for (uint8_t index = 0; index < 4; index++) {
    tokens[index] = strtok_r(NULL, " ", savePointer);
    if (tokens[index] == NULL || !parseLongExact(tokens[index], values[index]) || values[index] < 0) {
      printError(sequence, "MALFORMED_COMMAND");
      return;
    }
  }
  if (strtok_r(NULL, " ", savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
  if (values[0] > MAX_COMMAND_STEPS ||
      values[1] > (long)ABSOLUTE_MAX_SPEED_STEPS_S ||
      values[2] > (long)ABSOLUTE_MAX_ACCELERATION_STEPS_S2 ||
      values[3] > (long)ABSOLUTE_MAX_SPEED_STEPS_S) {
    printError(sequence, "OUT_OF_RANGE");
    return;
  }
  pendingMaximumTravelSteps = values[0];
  pendingMaximumSpeedStepsS = (unsigned long)values[1];
  pendingMaximumAccelerationStepsS2 = (unsigned long)values[2];
  pendingHomeSpeedStepsS = (unsigned long)values[3];
  pendingLimitsReceived = true;
  printAck(sequence, "CONFIG_LIMITS");
  printDone(sequence, "staged=true");
}

void handleConfigApply(long sequence, char **savePointer) {
  if (strtok_r(NULL, " ", savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
  if (moving || driverEnabled) { printError(sequence, "CONFIG_STATE"); return; }
  if (!pendingIoReceived || !pendingLimitsReceived) { printError(sequence, "CONFIG_INCOMPLETE"); return; }
  if (pendingLimitsCommissioned && !pendingMotionCommissioned) {
    printError(sequence, "CONFIG_INVALID"); return;
  }
  if (pendingMotionCommissioned &&
      (pendingMaximumSpeedStepsS == 0 || pendingMaximumAccelerationStepsS2 == 0)) {
    printError(sequence, "CONFIG_INVALID"); return;
  }
  if (pendingLimitsCommissioned &&
      (pendingMaximumTravelSteps <= 0 || pendingHomeSpeedStepsS == 0 ||
       pendingHomeSpeedStepsS > pendingMaximumSpeedStepsS)) {
    printError(sequence, "CONFIG_INVALID"); return;
  }
  if (runtimeIoPolarityLocked &&
      (pendingDriverSignalsInverted != DRIVER_SIGNALS_INVERTED ||
       pendingEnableActiveLow != ENABLE_ACTIVE_LOW ||
       pendingUpperLimitActiveHigh != UPPER_LIMIT_ACTIVE_HIGH ||
       pendingLowerLimitActiveHigh != LOWER_LIMIT_ACTIVE_HIGH)) {
    printError(sequence, "IO_POLARITY_LOCKED"); return;
  }

  bool configurationChanged =
      pendingMotionCommissioned != MOTION_COMMISSIONED ||
      pendingLimitsCommissioned != LIMITS_COMMISSIONED ||
      pendingDriverSignalsInverted != DRIVER_SIGNALS_INVERTED ||
      pendingEnableActiveLow != ENABLE_ACTIVE_LOW ||
      pendingUpperLimitActiveHigh != UPPER_LIMIT_ACTIVE_HIGH ||
      pendingLowerLimitActiveHigh != LOWER_LIMIT_ACTIVE_HIGH ||
      pendingMaximumTravelSteps != COMMISSIONED_MAX_TRAVEL_STEPS ||
      pendingMaximumSpeedStepsS != COMMISSIONED_MAX_SPEED_STEPS_S ||
      pendingMaximumAccelerationStepsS2 != COMMISSIONED_MAX_ACCELERATION_STEPS_S2 ||
      pendingHomeSpeedStepsS != COMMISSIONED_HOME_SPEED_STEPS_S;

  printAck(sequence, "CONFIG_APPLY");
  setDriverEnabled(false);
  stopPulseGeneration(configurationChanged);
  runtimeMotionCommissioned = pendingMotionCommissioned;
  runtimeLimitsCommissioned = pendingLimitsCommissioned;
  runtimeDriverSignalsInverted = pendingDriverSignalsInverted;
  runtimeEnableActiveLow = pendingEnableActiveLow;
  runtimeUpperLimitActiveHigh = pendingUpperLimitActiveHigh;
  runtimeLowerLimitActiveHigh = pendingLowerLimitActiveHigh;
  runtimeMaximumTravelSteps = pendingMaximumTravelSteps;
  runtimeMaximumSpeedStepsS = pendingMaximumSpeedStepsS;
  runtimeMaximumAccelerationStepsS2 = pendingMaximumAccelerationStepsS2;
  runtimeHomeSpeedStepsS = pendingHomeSpeedStepsS;
  digitalWrite(STEP_PIN, logicalOutput(false) ? HIGH : LOW);
  digitalWrite(DIR_PIN, logicalOutput(false) ? HIGH : LOW);
  if (configurationChanged) {
    homed = false;
    positionKnown = false;
    commandedPositionSteps = 0;
  }
  runtimeConfigured = true;
  runtimeIoPolarityLocked = true;
  pendingIoReceived = false;
  pendingLimitsReceived = false;
  printDone(sequence, "runtime_configured=true enabled=false position_known=false");
  Serial.println("EVENT CONFIG_APPLIED");
}

void handleCommand(char *line) {
  char *savePointer = NULL;
  char *sequenceText = strtok_r(line, " ", &savePointer);
  char *command = strtok_r(NULL, " ", &savePointer);
  long sequence = 0;
  if (!parseLongExact(sequenceText, sequence) || sequence < 1 || command == NULL) {
    printError(1, "MALFORMED_COMMAND");
    return;
  }
  uppercase(command);
  lastCommunicationMs = millis();

  if (strcmp(command, "CONFIG_IO") == 0) {
    handleConfigIo(sequence, &savePointer); return;
  }
  if (strcmp(command, "CONFIG_LIMITS") == 0) {
    handleConfigLimits(sequence, &savePointer); return;
  }
  if (strcmp(command, "CONFIG_APPLY") == 0) {
    handleConfigApply(sequence, &savePointer); return;
  }

  if (strcmp(command, "STOP") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printAck(sequence, "STOP");
    bool interrupted = moving;
    stopPulseGeneration(interrupted);
    activeSequence = 0;
    activeCommand[0] = '\0';
    if (interrupted) {
      printDone(sequence, "stopped=true position_known=false");
    } else {
      printDone(sequence, positionKnown ? "stopped=true position_known=true" : "stopped=true position_known=false");
    }
    Serial.print("EVENT STOP_RECEIVED interrupted=");
    Serial.println(interrupted ? "true" : "false");
    return;
  }
  if (strcmp(command, "PING") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printAck(sequence, "PING"); printDone(sequence, "PONG"); return;
  }
  if (strcmp(command, "IDENTITY") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printAck(sequence, "IDENTITY");
    Serial.print("DONE "); Serial.print(sequence);
    Serial.print(" device="); Serial.print(DEVICE_NAME);
    Serial.print(" board="); Serial.print(BOARD_NAME);
    Serial.print(" version="); Serial.print(FIRMWARE_VERSION);
    Serial.print(" driver="); Serial.println(DRIVER_MODEL);
    return;
  }
  if (strcmp(command, "STATUS") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printStatus(sequence); return;
  }
  if (strcmp(command, "LED") == 0) {
    char *value = strtok_r(NULL, " ", &savePointer);
    if (value == NULL || strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    uppercase(value);
    if (strcmp(value, "ON") != 0 && strcmp(value, "OFF") != 0) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printAck(sequence, strcmp(value, "ON") == 0 ? "LED ON" : "LED OFF");
    setLed(strcmp(value, "ON") == 0);
    printDone(sequence, ledOn ? "led=on" : "led=off"); return;
  }
  if (strcmp(command, "BLINK") == 0) {
    if (blinkActive || strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, blinkActive ? "BUSY" : "MALFORMED_COMMAND"); return; }
    printAck(sequence, "BLINK");
    setLed(false); blinkActive = true; blinkTransitionsRemaining = 6;
    lastBlinkTransitionMs = millis(); blinkSequence = sequence; return;
  }
  if (strcmp(command, "CLEAR_FAULT") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    if (moving) { printError(sequence, "BUSY"); return; }
    faultLatched = false; strcpy(faultCode, "NONE");
    printAck(sequence, "CLEAR_FAULT"); printDone(sequence, "fault=NONE"); return;
  }
  if (strcmp(command, "DISABLE") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printAck(sequence, "DISABLE");
    if (moving) stopPulseGeneration(true);
    setDriverEnabled(false); printDone(sequence, "enabled=false"); return;
  }
  if (strcmp(command, "ENABLE") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    if (!runtimeConfigured) { printError(sequence, "CONFIG_REQUIRED"); return; }
    if (!MOTION_COMMISSIONED) { printError(sequence, "MOTION_NOT_COMMISSIONED"); return; }
    if (faultLatched) { printError(sequence, "FAULT_LATCHED"); return; }
    printAck(sequence, "ENABLE"); setDriverEnabled(true); printDone(sequence, "enabled=true"); return;
  }
  if (strcmp(command, "HOME") == 0) {
    if (strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    printError(sequence, "PHYSICAL_HOME_UNAVAILABLE"); return;
  }
  if (strcmp(command, "JOG") == 0 || strcmp(command, "MOVE_ABS") == 0) {
    char *positionText = strtok_r(NULL, " ", &savePointer);
    char *speedText = strtok_r(NULL, " ", &savePointer);
    if (positionText == NULL || speedText == NULL || strtok_r(NULL, " ", &savePointer) != NULL) { printError(sequence, "MALFORMED_COMMAND"); return; }
    long positionOrSteps = 0, speed = 0;
    if (!parseLongExact(positionText, positionOrSteps) || !parseLongExact(speedText, speed) || speed <= 0) { printError(sequence, "MALFORMED_COMMAND"); return; }
    if (strcmp(command, "MOVE_ABS") == 0) {
      printError(sequence, "USE_HOST_LOGICAL_POSITION"); return;
    } else {
      beginMovement(sequence, "JOG", positionOrSteps, (unsigned long)speed, false);
    }
    return;
  }
  printError(sequence, "UNKNOWN_COMMAND");
}

void serviceSerial() {
  uint8_t processed = 0;
  while (Serial.available() > 0 && processed < MAX_SERIAL_BYTES_PER_LOOP) {
    processed++;
    char incoming = (char)Serial.read();
    if (incoming == '\r') continue;
    if (incoming == '\n') {
      if (discardingOversizedLine) {
        printError(1, "INPUT_TOO_LONG");
      } else if (inputLength > 0) {
        inputBuffer[inputLength] = '\0';
        handleCommand(inputBuffer);
      }
      inputLength = 0;
      discardingOversizedLine = false;
      continue;
    }
    if (discardingOversizedLine) continue;
    if (inputLength >= INPUT_CAPACITY - 1) {
      inputLength = 0;
      discardingOversizedLine = true;
      continue;
    }
    inputBuffer[inputLength++] = incoming;
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  // Preload all motion-output latches before setting OUTPUT mode. This avoids
  // transitions when a reviewed interface inverts the logical signal.
  digitalWrite(STEP_PIN, logicalOutput(false) ? HIGH : LOW);
  digitalWrite(DIR_PIN, logicalOutput(false) ? HIGH : LOW);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  setLed(false);
  setDriverEnabled(false);
  Serial.begin(115200);
  lastCommunicationMs = millis();
}

void loop() {
  serviceSerialConnection();
  if (readyAnnouncedForHost) serviceSerial();
  serviceMovement();
  serviceBlink();
}
