# Dashboard-Hardware

#### Install VeneMQ

```bash
docker run -e "DOCKER_VERNEMQ_ALLOW_ANONYMOUS=on" -p 1883:1883 -e "DOCKER_VERNEMQ_ACCEPT_EULA=yes" --name vernemq -d vernemq/vernemq
```