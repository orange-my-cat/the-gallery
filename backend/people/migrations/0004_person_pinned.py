from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0003_face_ignored_because'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='pinned',
            field=models.BooleanField(default=False),
        ),
    ]
