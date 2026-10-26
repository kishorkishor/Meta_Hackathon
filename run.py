"""Start the app:  python run.py   then open http://127.0.0.1:8770"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.api:app", host="127.0.0.1", port=8770)
