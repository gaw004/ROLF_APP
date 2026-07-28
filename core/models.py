from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base: adds created/updated timestamps to any model that inherits it."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="time created")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="time updated")

    class Meta:
        abstract = True
