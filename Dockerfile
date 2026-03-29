# Use the official Playwright image — includes Python + Chromium pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create temp directory
RUN mkdir -p .tmp

# Expose port
EXPOSE 8080

# Start Flask
CMD ["python", "app.py"]
