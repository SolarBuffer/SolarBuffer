from flask import Flask, session, redirect, url_for, request, jsonify
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a strong secret key

def load_config():
    with open('config.json') as config_file:
        return json.load(config_file)

def login_required(f):
    """ Decorator to check if a user is logged in. """
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        config = load_config()

        # Validate the credentials against config.json
        user_data = config.get('users', {}).get(username)
        if user_data and user_data['password'] == password:
            session['username'] = username
            return redirect(url_for('dashboard'))
        
        return 'Invalid username or password', 401

    return '''
        <form method="post">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <input type="submit" value="Login">
        </form>
    '''

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    return 'Welcome to your dashboard, {}'.format(session['username'])

@app.route('/wizard')
@login_required
def wizard():
    return 'Welcome to the wizard, {}'.format(session['username'])

if __name__ == '__main__':
    app.run(debug=True)