#
# NEW DOCKERFILE (Final version with correct drivers)
#
# Start from the official jrottenberg/ffmpeg image
FROM jrottenberg/ffmpeg:7.1-vaapi2404

# We are root by default
USER root

# 1. Update apt and install Python3 AND the Intel drivers
# The 'jrottenberg' base image already has the non-free repos enabled
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    vainfo \
    intel-media-va-driver-non-free \
&& rm -rf /var/lib/apt/lists/*

# 2. Set up the working directory
WORKDIR /app

# 3. Copy and install your Python requirements
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 4. Copy the rest of your application code
COPY . .

# Clear the base image's ENTRYPOINT
ENTRYPOINT []

# 5. Set the default command to run your app
CMD ["python3", "app.py"]