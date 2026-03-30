from flask import Flask, session, redirect, url_for, request, abort
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Set your secret key

# Configure Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)

# Load user configuration
with open('config.json') as config_file:
    config = json.load(config_file)

# User loader
@login_manager.user_loader
def load_user(username):
    if username in config['users']:
        return config['users'][username]
    return None

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in config['users'] and config['users'][username]['password'] == password:
            user_obj = config['users'][username]
            login_user(user_obj)
            return redirect(url_for('protected_route'))
    return 'Login Form'

# Logout route
@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

# Protected route
@app.route('/protected')
@login_required
def protected_route():
    return 'Logged in as: {}'.format(current_user.username)

# Your PID/Shelly control loop here
# ...

if __name__ == '__main__':
    app.run()