from flask import Flask, request
from dotenv import load_dotenv
import os
import json
import sqlite3
from groq import Groq
import logging
import random
import africastalking

app = Flask(__name__)
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Groq client initialization
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Africa's Talking initialization
username = os.getenv("AT_USERNAME")
api_key = os.getenv("AT_API_KEY")
if not all([username, api_key]):
    raise ValueError("Africa's Talking credentials not found. Set AT_USERNAME and AT_API_KEY in .env")
africastalking.initialize(username, api_key)
sms = africastalking.SMS

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        phone TEXT PRIMARY KEY,
        grade INTEGER,
        subject TEXT,
        points INTEGER DEFAULT 0,
        lives INTEGER DEFAULT 3,
        current_question INTEGER DEFAULT 0,
        session_questions TEXT DEFAULT '[]'
    )""")
    conn.commit()
    conn.close()

init_db()

def generate_question():
    random_seed = random.randint(1, 1000)
    prompt = f"Generate a unique Grade 4 Math question (Kenyan curriculum, addition/subtraction). " \
             f"Use a varied context (e.g., shopping, farming, travel, school) and ensure the question is distinct (seed: {random_seed}). " \
             f"Provide a multiple-choice question with 4 options and correct answer. " \
             f"Format: Question|OptionA|OptionB|OptionC|OptionD|Correct"
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile"
        )
        response = chat_completion.choices[0].message.content.strip()
        logger.debug(f"Raw API response: {response}")

        parts = response.split("|")
        if len(parts) != 6 or not all(parts):
            logger.error(f"Invalid response format: {response}")
            return {
                "question": "What is 300 + 200?",
                "options": ["400", "500", "600", "700"],
                "correct": "500"
            }

        question, a, b, c, d, correct = parts
        return {
            "question": question,
            "options": [a, b, c, d],
            "correct": correct
        }
    except Exception as e:
        logger.error(f"Error generating question: {str(e)}")
        return {
            "question": "What is 300 + 200?",
            "options": ["400", "500", "600", "700"],
            "correct": "500"
        }

def generate_unique_session_questions(num_questions=5):
    session_questions = []
    question_texts = set()

    while len(session_questions) < num_questions:
        question = generate_question()
        if question["question"] not in question_texts:
            question_texts.add(question["question"])
            session_questions.append(question)
        else:
            logger.debug(f"Duplicate question detected: {question['question']}, regenerating...")
    
    return session_questions

def evaluate_answer(question_data, user_answer):
    prompt = f"Evaluate if '{user_answer}' is correct for '{question_data['question']}' " \
             f"with options {question_data['options']}. Correct: {question_data['correct']}. " \
             f"Provide feedback (under 100 chars) if incorrect."
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile"
        )
        response = chat_completion.choices[0].message.content.strip()
        is_correct = "Correct" in response
        return {"is_correct": is_correct, "feedback": response}
    except Exception as e:
        return {"is_correct": False, "feedback": f"Error: {str(e)}"}

# Africa's Talking USSD endpoint
@app.route("/ussd", methods=["POST"])
def ussd_callback():
    session_id = request.values.get("sessionId", None)
    service_code = request.values.get("serviceCode", None)
    phone_number = request.values.get("phoneNumber", None)
    text = request.values.get("text", "")

    if not all([session_id, service_code, phone_number]):
        return "END Invalid request parameters", 400

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = ?", (phone_number,))
    user = c.fetchone()

    if not user:
        c.execute("INSERT INTO users (phone, grade, subject) VALUES (?, ?, ?)", (phone_number, 4, "Math"))
        conn.commit()
        user = (phone_number, 4, "Math", 0, 3, 0, "[]")

    _, grade, subject, points, lives, current_question, session_questions = user
    session_questions = json.loads(session_questions)

    response = "CON "
    if text == "":
        response += "Welcome to LearnEasy! Reply:\n1) Start Math\n2) View Points\n3) Exit"
    elif text == "1":
        if not session_questions:
            session_questions = generate_unique_session_questions(5)
            c.execute("UPDATE users SET lives = 3, current_question = 0, session_questions = ? WHERE phone = ?",
                      (json.dumps(session_questions), phone_number))
            conn.commit()
        question = session_questions[current_question]
        response += f"Q{current_question + 1}: {question['question']}\n" \
                    f"A) {question['options'][0]}\nB) {question['options'][1]}\n" \
                    f"C) {question['options'][2]}\nD) {question['options'][3]}\nReply with A, B, C, or D"
    elif text in ["A", "B", "C", "D"]:
        if not session_questions:
            response = "END No active session. Start a new game with 1."
        else:
            question = session_questions[current_question]
            result = evaluate_answer(question, text)
            if result["is_correct"]:
                points += 10
                feedback = "Correct! +10 points."
            else:
                lives -= 1
                feedback = result["feedback"]
                if lives == 0:
                    c.execute("UPDATE users SET session_questions = '[]', current_question = 0 WHERE phone = ?",
                              (phone_number,))
                    conn.commit()
                    try:
                        sms_response = sms.send(f"Game Over! Your score: {points}. Dial USSD to play again!", [phone_number])
                        logger.debug(f"SMS sent: {sms_response}")
                    except Exception as e:
                        logger.error(f"Failed to send SMS: {str(e)}")
                    response = f"END Game Over! Score: {points}\nDial again to play."
                    conn.close()
                    return response, 200, {"Content-Type": "text/plain"}
            current_question += 1
            if current_question >= len(session_questions):
                c.execute("UPDATE users SET points = ?, session_questions = '[]', current_question = 0 WHERE phone = ?",
                          (points, phone_number))
                conn.commit()
                try:
                    sms_response = sms.send(f"Session Complete! Your score: {points}. Dial USSD to play again!", [phone_number])
                    logger.debug(f"SMS sent: {sms_response}")
                except Exception as e:
                    logger.error(f"Failed to send SMS: {str(e)}")
                response = f"END Session complete! Score: {points}\nDial again to play."
            else:
                c.execute("UPDATE users SET points = ?, lives = ?, current_question = ? WHERE phone = ?",
                          (points, lives, current_question, phone_number))
                conn.commit()
                question = session_questions[current_question]
                response += f"{feedback}\nQ{current_question + 1}: {question['question']}\n" \
                            f"A) {question['options'][0]}\nB) {question['options'][1]}\n" \
                            f"C) {question['options'][2]}\nD) {question['options'][3]}\nReply with A, B, C, or D"
    elif text == "2":
        response += f"Your Points: {points}\nReply:\n1) Start Math\n3) Exit"
    elif text == "3":
        response = "END Thank you for using LearnEasy!"
    else:
        response = "END Invalid input. Try again."

    conn.close()
    return response, 200, {"Content-Type": "text/plain"}

# Web endpoint
@app.route("/web", methods=["GET", "POST"])
def web():
    phone = request.args.get("phone", "default_user")
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    user = c.fetchone()

    if not user:
        c.execute("INSERT INTO users (phone, grade, subject) VALUES (?, ?, ?)", (phone, 4, "Math"))
        conn.commit()
        user = (phone, 4, "Math", 0, 3, 0, "[]")

    _, grade, subject, points, lives, current_question, session_questions = user
    session_questions = json.loads(session_questions)

    if request.method == "POST":
        user_answer = request.form.get("answer")
        if user_answer and session_questions:
            question = session_questions[current_question]
            result = evaluate_answer(question, user_answer)
            if result["is_correct"]:
                points += 10
            else:
                lives -= 1
            current_question += 1
            if lives == 0 or current_question >= len(session_questions):
                c.execute("UPDATE users SET points = ?, session_questions = '[]', current_question = 0 WHERE phone = ?",
                          (points, phone))
                conn.commit()
                conn.close()
                return f"Game Over! Score: {points} <a href='/web?phone={phone}'>Play Again</a>"
            c.execute("UPDATE users SET points = ?, lives = ?, current_question = ? WHERE phone = ?",
                      (points, lives, current_question, phone))
            conn.commit()

    if not session_questions:
        session_questions = generate_unique_session_questions(5)
        c.execute("UPDATE users SET lives = 3, current_question = 0, session_questions = ? WHERE phone = ?",
                  (json.dumps(session_questions), phone))
        conn.commit()

    question = session_questions[current_question]
    conn.close()
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>LearnEasy</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1 {{ font-size: 24px; }}
            p {{ font-size: 16px; }}
            form {{ margin-top: 20px; }}
            input[type="radio"] {{ margin: 10px 0; }}
            input[type="submit"] {{ padding: 10px; background: #28a745; color: white; border: none; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h1>LearnEasy: Math Grade 4</h1>
        <p>Points: {points} | Lives: {lives}</p>
        <h3>Q{current_question + 1}: {question['question']}</h3>
        <form method="POST">
            <input type="radio" name="answer" value="A"> {question['options'][0]}<br>
            <input type="radio" name="answer" value="B"> {question['options'][1]}<br>
            <input type="radio" name="answer" value="C"> {question['options'][2]}<br>
            <input type="radio" name="answer" value="D"> {question['options'][3]}<br>
            <input type="submit" value="Submit">
        </form>
    </body>
    </html>
    """

def main():
    print("Welcome to LearnEasy! Enter your phone number to start (e.g., test123):")
    phone = input().strip()

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    user = c.fetchone()

    if not user:
        c.execute("INSERT INTO users (phone, grade, subject) VALUES (?, ?, ?)", (phone, 4, "Math"))
        conn.commit()
        user = (phone, 4, "Math", 0, 3, 0, "[]")

    _, grade, subject, points, lives, current_question, session_questions = user
    session_questions = json.loads(session_questions)

    while True:
        print("\nMenu: 1) Start Math  2) View Points  3) Exit")
        choice = input("Enter choice (1-3): ").strip()

        if choice == "1":
            if not session_questions:
                session_questions = generate_unique_session_questions(5)
                lives = 3
                current_question = 0
                c.execute("UPDATE users SET lives = ?, current_question = ?, session_questions = ? WHERE phone = ?",
                          (lives, current_question, json.dumps(session_questions), phone))
                conn.commit()

            while current_question < len(session_questions) and lives > 0:
                question = session_questions[current_question]
                print(f"\nQ{current_question + 1}: {question['question']}")
                print(f"A) {question['options'][0]}  B) {question['options'][1]}")
                print(f"C) {question['options'][2]}  D) {question['options'][3]}")
                user_answer = input("Enter answer (A, B, C, D): ").strip().upper()

                if user_answer not in ["A", "B", "C", "D"]:
                    print("Invalid answer! Please enter A, B, C, or D.")
                    continue

                result = evaluate_answer(question, user_answer)
                if result["is_correct"]:
                    points += 10
                    print("Correct! +10 points.")
                else:
                    lives -= 1
                    print(f"Incorrect. {result['feedback']}")
                    print(f"Lives remaining: {lives}")

                current_question += 1
                c.execute("UPDATE users SET points = ?, lives = ?, current_question = ? WHERE phone = ?",
                          (points, lives, current_question, phone))
                conn.commit()

                if lives == 0:
                    print(f"Game Over! Final Score: {points}")
                    c.execute("UPDATE users SET session_questions = '[]', current_question = 0 WHERE phone = ?",
                              (phone,))
                    conn.commit()
                    break
                elif current_question >= len(session_questions):
                    print(f"Session Complete! Final Score: {points}")
                    c.execute("UPDATE users SET session_questions = '[]', current_question = 0 WHERE phone = ?",
                              (phone,))
                    conn.commit()
                    break

        elif choice == "2":
            print(f"Your Points: {points}")

        elif choice == "3":
            print("Thank you for using LearnEasy!")
            break

        else:
            print("Invalid choice! Please enter 1, 2, or 3.")

    conn.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)