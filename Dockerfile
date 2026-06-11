# Stage 1: Build the React webapp
FROM node:20-alpine AS frontend-builder
WORKDIR /app/webapp
COPY webapp/package*.json ./
RUN npm install
COPY webapp/ ./
RUN npm run build

# Stage 2: Build the Python backend
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy Deno binary to serve as the JavaScript runtime for yt-dlp
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno


# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend files
COPY src/ ./src/
COPY main.py .

# Copy built frontend files from Stage 1
COPY --from=frontend-builder /app/webapp/dist ./webapp/dist

# Expose Web API port
EXPOSE 8080

# Run the bot application
CMD ["python", "main.py"]
