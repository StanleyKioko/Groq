from flask import Flask, request
import africastalking
import os

app = Flask(__name__)

# Retrieve sensitive information from environment variables
username = "evaankahenya"
api_key = "atsk_a4651edb99211b9f8c0ec176745189172379c25ab89af7cc33602b2cb4ad282cdad8ee93"

if not api_key:
    raise ValueError("API key not found. Set the AFRICASTALKING_API_KEY environment variable.")

africastalking.initialize(username, api_key)
sms = africastalking.SMS

@app.route('/', methods=['POST', 'GET'])
def ussd_callback():
    global response
    session_id = request.values.get("sessionId", None)
    service_code = request.values.get("serviceCode", None)
    phone_number = request.values.get("phoneNumber", None)
    text = request.values.get("text", "default")
    sms_phone_number = []
    sms_phone_number.append(phone_number)

    # USSD logic
    if text == "":
        # Main menu
        response = "CON What would you like to do?\n"
        response += "1. Check account details\n"
        response += "2. Check phone number\n"
        response += "3. Send me a cool message"
    elif text == "1":
        # Sub menu 1
        response = "CON What would you like to check on your account?\n"
        response += "1. Account number\n"
        response += "2. Account balance"
    elif text == "2":
        # Sub menu 2
        response = "END Your phone number is {}".format(phone_number)
    elif text == "3":
        try:
            # Sending the SMS
            sms_response = sms.send("Thank you for going through this tutorial", sms_phone_number)
            print(sms_response)
        except Exception as e:
            # Show us what went wrong
            print(f"Houston, we have a problem: {e}")
    elif text == "1*1":
        # USSD menus are split using *
        account_number = "1243324376742"
        response = "END Your account number is {}".format(account_number)
    elif text == "1*2":
        account_balance = "100,000"
        response = "END Your account balance is USD {}".format(account_balance)
    else:
        response = "END Invalid input. Try again."

    return response

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=os.environ.get("PORT"))