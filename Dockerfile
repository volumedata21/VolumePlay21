# Use a modern, slim Python base image
FROM python:3.11-slim

# --- NEW ---
# Install ffmpeg for thumbnail generation and clean up apt cache
RUN apt-get update && apt-get install -y ffmpeg --no-install-recommends && rm -rf /var/lib/apt/lists/*
# --- END NEW ---

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
# We add gunicorn as a production-ready web server
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
# (app.py, templates/, static/)
COPY . .

# Expose the port the app will run on
EXPOSE 5000

# Set the command to run the application using gunicorn
# This is more robust than `flask run` for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]