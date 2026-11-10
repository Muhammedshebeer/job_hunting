from django.conf import settings

for key in [
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USE_TLS",
    "EMAIL_USE_SSL",
    "DEFAULT_FROM_EMAIL",
]:
    print(key, "=", getattr(settings, key, None))

print("EMAIL_HOST_USER configured:", bool(getattr(settings, "EMAIL_HOST_USER", None)))
print("EMAIL_HOST_PASSWORD configured:", bool(getattr(settings, "EMAIL_HOST_PASSWORD", None)))
