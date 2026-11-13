from django.apps import AppConfig


class DuplicatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "duplicates"

    def ready(self) -> None:
        # The operation engine finds step types by name; register ours in
        # every process, web and worker alike.
        from duplicates.services import steps  # noqa: F401
