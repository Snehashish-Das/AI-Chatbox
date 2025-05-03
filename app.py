from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_session import Session
import google.generativeai as genai
import sqlite3
import os
from datetime import datetime
from bs4 import BeautifulSoup
import markdown2

app = Flask(__name__)
app.secret_key = "INSERT YOUR OWN"

# Server-side Session Setup --------------------------------------
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './flask_session_data'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
Session(app)

# Google Gemini Setup --------------------------------------------
genai.configure(api_key="INSERT YOUR OWN")
model = genai.GenerativeModel("models/gemini-2.5-pro-exp-03-25")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Database Setup -------------------------------------------------
def init_db():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            )
        ''')
        conn.commit()

app.config['DATABASE'] = 'users.db'
if not os.path.exists(app.config['DATABASE']):
    init_db()

# User Model --------------------------------------------------------
class User(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM users WHERE username = ?', (user_id,))
        user = cursor.fetchone()
        if user:
            return User(user[0])
    return None

def get_user_id(username):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        return user[0] if user else None

def clean_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    allowed_tags = [
        'b', 'i', 'strong', 'em', 'code', 'pre', 'br', 'p', 'ul', 'ol', 'li',
        'table', 'thead', 'tbody', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
    ]
    allowed_attrs = {'class', 'style', 'align', 'colspan', 'rowspan'}
    for tag in soup.find_all(True):
        if tag.name not in allowed_tags:
            tag.unwrap()
        else:
            tag.attrs = {k: v for k, v in tag.attrs.items() if k in allowed_attrs}
    return str(soup)

def get_conversations(user_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, title, created_at 
            FROM conversations 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        return cursor.fetchall()

def get_messages(conversation_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sender, content 
            FROM messages 
            WHERE conversation_id = ? 
            ORDER BY timestamp ASC
        ''', (conversation_id,))
        return cursor.fetchall()

def create_new_conversation(user_id, title="New Chat"):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (user_id, title) 
            VALUES (?, ?)
        ''', (user_id, title))
        conn.commit()
        return cursor.lastrowid

def add_message(conversation_id, sender, content):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO messages (conversation_id, sender, content) 
            VALUES (?, ?, ?)
        ''', (conversation_id, sender, content))
        conn.commit()

def update_conversation_title(conversation_id, title):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE conversations 
            SET title = ? 
            WHERE id = ?
        ''', (title, conversation_id))
        conn.commit()

def delete_conversation(conversation_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
        cursor.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        conn.commit()

# Routes
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("chat"))

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        with sqlite3.connect(app.config['DATABASE']) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT username, password FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            if user and user[1] == password:
                login_user(User(username))
                return redirect(url_for("chat"))
        flash("Invalid credentials")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("chat"))

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            flash("Passwords don't match!")
            return redirect(url_for("signup"))

        try:
            with sqlite3.connect(app.config['DATABASE']) as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
                conn.commit()
            flash("Account created successfully! Please login.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists!")
    return render_template("signup.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():
    user_id = get_user_id(current_user.id)
    conversations = get_conversations(user_id)
    
    conversation_id = request.args.get('conversation_id')
    if not conversation_id and conversations:
        conversation_id = conversations[0][0]
    
    if request.args.get('new_chat'):
        conversation_id = create_new_conversation(user_id)
        return redirect(url_for('chat', conversation_id=conversation_id))
    
    if request.args.get('delete_conversation'):
        delete_conversation(conversation_id)
        return redirect(url_for('chat'))
    
    if request.args.get('rename_conversation'):
        new_title = request.args.get('new_title')
        if new_title and conversation_id:
            update_conversation_title(conversation_id, new_title)
        return redirect(url_for('chat', conversation_id=conversation_id))
    
    if request.method == "POST":
        user_input = request.form.get("user_input", "").strip()
        if user_input and conversation_id:
            messages = get_messages(conversation_id)
            if not messages:
                try:
                    title_response = model.generate_content(f"Generate a 1 word title for this conversation starter: {user_input}")
                    title = title_response.text.strip().replace('"', '')[:30]
                    update_conversation_title(conversation_id, title)
                except:
                    update_conversation_title(conversation_id, "Chat")
            
            add_message(conversation_id, "user", user_input)
            
            try:
                response = model.generate_content(user_input)
                html_content = markdown2.markdown(
                    response.text.strip(), 
                    extras=[
                        "fenced-code-blocks", 
                        "code-friendly", 
                        "tables",
                        "wiki-tables",
                        "cuddled-lists",
                        "strike"
                    ]
                )
                cleaned_html = clean_html(html_content)
                add_message(conversation_id, "bot", cleaned_html)
            except Exception as e:
                print(f"Error: {e}")
                add_message(conversation_id, "bot", "⚠️ Sorry, something went wrong. Please try again.")
            
            return redirect(url_for('chat', conversation_id=conversation_id))
    
    messages = []
    if conversation_id:
        messages = get_messages(conversation_id)
    
    return render_template("index.html", 
                         chat_history=messages,
                         username=current_user.id,
                         conversations=conversations,
                         current_conversation=conversation_id)

if __name__ == "__main__":
    if not os.path.exists('./flask_session_data'):
        os.makedirs('./flask_session_data')
    app.run(debug=True)
