from django.db import migrations, models


def number_existing_pins(apps, schema_editor):
    """Keep pins made before ordering in the order they were shown: by name."""
    Person = apps.get_model("people", "Person")
    pinned = sorted(Person.objects.filter(pinned=True), key=lambda p: (not p.name, p.name.lower(), p.pk))
    for i, person in enumerate(pinned):
        Person.objects.filter(pk=person.pk).update(pin_order=i)


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0004_person_pinned'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='pin_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(number_existing_pins, migrations.RunPython.noop),
    ]
