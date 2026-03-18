# GCSE Citizenship Revision App

A web app to help students revise for GCSE Citizenship. Ask questions from past papers, get AI-powered marking with detailed feedback, and track progress over time.

---

## Running locally on your Mac

### 1. Get the code

Open Terminal (press Cmd+Space, type "Terminal", press Enter) and run:

```bash
git clone <your-repo-url> Citizenship
cd Citizenship
```

Or if you already have the folder, just open Terminal and navigate to it:

```bash
cd ~/path/to/Citizenship
```

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

If you get a permissions error, try:
```bash
pip3 install -r requirements.txt --user
```

### 3. Add your Anthropic API key

Create a file called `.env` in the project folder:

```bash
cp .env.example .env
```

Then open `.env` in a text editor and replace `your_api_key_here` with your actual API key from [console.anthropic.com](https://console.anthropic.com).

It should look like:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Start the app

```bash
python3 app.py
```

You should see:
```
GCSE Citizenship Revision App
Open http://localhost:5000 in your browser
```

Open [http://localhost:5000](http://localhost:5000) in Safari or Chrome. Done!

---

## Using the app

1. **Add your students** on the home page (click "Add a student")
2. **Add your past paper questions** via the Questions page → Import tab → upload your PDFs
3. **Start a session** — click a student's tile, choose a topic and number of questions
4. **Answer questions** — write your answer, submit, get detailed AI feedback
5. **Track progress** — click "View Progress" to see charts and weak areas

---

## Deploying to the web (Railway)

Railway is the easiest way to host this app publicly so it's accessible from any device.

### 1. Create a Railway account

Go to [railway.app](https://railway.app) and sign up (free tier available).

### 2. Add a Procfile

Create a file called `Procfile` (no extension) in the project folder:

```
web: python app.py
```

And update `app.py` to use the PORT environment variable — change the last line from:
```python
app.run(debug=True, host="0.0.0.0", port=5000)
```
to:
```python
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
```

### 3. Add a volume for persistent data

In Railway, add a volume mounted at `/app/data` so the database and question bank survive redeployments.

### 4. Set environment variables

In Railway project settings → Variables, add:
- `ANTHROPIC_API_KEY` = your API key
- `SECRET_KEY` = any random string (e.g. run `python3 -c "import secrets; print(secrets.token_hex())"`)

### 5. Deploy

Connect your GitHub repo in Railway and it will deploy automatically.

---

## Adding past paper questions

Go to **Questions → Import** in the app:

1. Enter the paper name (e.g. "June 2023") and code (e.g. "J560/01")
2. Upload the question paper PDF and the mark scheme PDF
3. Claude reads both and extracts all questions automatically (~30 seconds)
4. Review the preview, then click "Add to Question Bank"

Repeat for each past paper.

---

## File structure

```
app.py                 # Main application (Flask)
config.py              # Configuration and topic definitions
requirements.txt       # Python dependencies
.env                   # Your API key (not committed to git)
data/
  questions.json       # Question bank
  citizenship.db       # Student progress database
templates/             # HTML pages
static/                # CSS and JavaScript
scripts/
  import_questions.py  # Alternative: command-line PDF extraction
```
