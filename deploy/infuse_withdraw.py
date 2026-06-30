import time
import serial

# --- SETUP VARIABLES ---
PORT_NAME = "COM4"
BAUD_RATE = 115200
TIMEOUT_SECONDS = 2

def run_infuse_withdraw_sequence():
    print("=" * 60)
    print("     CHEMYX FUSION 4000-X: INFUSE & WITHDRAW AUTOMATION")
    print("=" * 60)
    
    ser = None
    try:
        # STEP 1: Connect to Hardware
        print(f"\n[STEP 1] Connecting to pump on {PORT_NAME}...")
        ser = serial.Serial(
            port=PORT_NAME,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT_SECONDS
        )
        
        if ser.is_open:
            print("SUCCESS: Connection open!")
        else:
            print("ERROR: Connection failed.")
            return

        # Central command sender helper function
        def send_cmd(command_text):
            print(f" -> Sending: '{command_text}'")
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            ser.write(f"{command_text}\r".encode('ascii'))
            time.sleep(0.2)
            
            raw = ser.read_all()
            decoded = raw.decode('ascii', errors='replace').strip()
            print(f" <- Response: '{decoded}'")
            return decoded

        # STEP 2: Configure Universal Syringe Properties (Channel 1)
        print("\n[STEP 2] Initializing Channel 1 Settings...")
        send_cmd("1 set units 0")        # mL/min
        send_cmd("1 set diameter 28.6")  # Test syringe size in mm
        send_cmd("1 set rate 2.0")       # Flow rate speed (2.0 mL/min)

        # STEP 3: Execute Infusion Cycle (Pumping Out)
        print("\n[STEP 3] Configuring positive volume target for INFUSION...")
        send_cmd("1 set volume 1.5")     # Positive number = Push fluid out
        
        print("\n[START] Launching Infusion Motor...")
        send_cmd("1 start 0")
        
        print("\n>>> Pumping out fluid for 10 seconds... <<<")
        for sec in range(1, 11):
            time.sleep(1)
            print(f"  [INFUSING] Second {sec}/10")

        # STEP 4: Mid-Point Safety Stop
        print("\n[STEP 4] Stopping pump motor to switch gears safely...")
        send_cmd("stop")
        print("Holding brake position for 2 seconds...")
        time.sleep(2)

        # STEP 5: Execute Withdrawal Cycle (Reversing In)
        print("\n[STEP 5] Configuring negative volume target for WITHDRAWAL...")
        # A negative symbol tells the Chemyx motherboard to spin the lead screw backward
        send_cmd("1 set volume -1.5")    
        
        print("\n[REVERSE] Launching Withdrawal Motor...")
        send_cmd("1 start 0")
        
        print("\n>>> Withdrawing fluid for 10 seconds... <<<")
        for sec in range(1, 11):
            time.sleep(1)
            print(f"  [WITHDRAWING] Second {sec}/10")

        # STEP 6: Final Shutdown Sequence
        print("\n[STEP 6] Sequence complete. Powering down motor...")
        send_cmd("stop")
        print("SUCCESS: Target workflow executed successfully.")

    except Exception as e:
        print(f"\n[ERROR] Sequence aborted due to exception: {e}")
        
    finally:
        if ser and ser.is_open:
            ser.close()
            print("\n[STEP 7] Serial loop safely closed.")
        print("=" * 60)

if __name__ == "__main__":
    run_infuse_withdraw_sequence()