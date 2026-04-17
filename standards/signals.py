from collections.abc import Iterable

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from students.models import Student, StudentClass
from standards.models import Standard, Level, StudentStandard


def _bulk_create_missing_student_standards(
    students,
    standards,
    level_numbers: Iterable[int],
):
    """Создает строки StudentStandard для комбинаций (студент, норматив, уровень), которые не существуют.
    """
    students = list(students)
    standards = list(standards)
    level_numbers = list(level_numbers)
    if not students or not standards or not level_numbers:
        return

    standard_ids = [s.id for s in standards]
    student_ids = [s.id for s in students]
    genders = {s.gender for s in students}

    level_map = {
        (level.standard_id, level.level_number, level.gender): level
        for level in Level.objects.filter(
            standard_id__in=standard_ids,
            level_number__in=level_numbers,
            gender__in=genders,
        )
    }
    if not level_map:
        return

    existing = set(
        StudentStandard.objects.filter(
            student_id__in=student_ids,
            standard_id__in=standard_ids,
            level__level_number__in=level_numbers,
        ).values_list('student_id', 'standard_id', 'level_id')
    )

    to_create = []
    for student in students:
        for standard in standards:
            for level_number in level_numbers:
                level = level_map.get((standard.id, level_number, student.gender))
                if not level:
                    continue
                if (student.id, standard.id, level.id) in existing:
                    continue
                to_create.append(
                    StudentStandard(
                        student=student,
                        standard=standard,
                        level=level,
                        value=None,
                        grade=None,
                    )
                )

    if to_create:
        StudentStandard.objects.bulk_create(to_create, batch_size=1000)


@receiver(pre_save, sender=Student)
def check_class_change(sender, instance, **kwargs):
    """Создает отсутствующие строки в таблице StudentStandard при переходе учащегося в следующий класс."""
    if not instance.pk:
        return
    try:
        old = Student.objects.only('student_class').get(pk=instance.pk)
    except Student.DoesNotExist:
        return
    old_number = old.student_class.number
    new_number = instance.student_class.number
    if new_number <= old_number:
        return

    standards = Standard.objects.filter(who_added=instance.student_class.class_owner)
    _bulk_create_missing_student_standards(
        students=[instance],
        standards=standards,
        level_numbers=range(old_number + 1, new_number + 1),
    )


@receiver(post_save, sender=StudentClass)
def handle_student_class_change(sender, instance, **kwargs):
    """Проверка, что для каждого учащегося данного класса на текущем уровне имеются строки в таблице StudentStandard."""
    students = list(Student.objects.filter(student_class=instance))
    if not students:
        return
    standards = Standard.objects.filter(who_added=instance.class_owner)
    _bulk_create_missing_student_standards(
        students=students,
        standards=standards,
        level_numbers=[instance.number],
    )


@receiver(post_save, sender=Student)
def create_student_standards(sender, instance, created, **kwargs):
    """При создании студента, заполняет строки StudentStandard для каждого номера класса до текущего."""
    if not created:
        return
    standards = Standard.objects.filter(who_added=instance.student_class.class_owner)
    _bulk_create_missing_student_standards(
        students=[instance],
        standards=standards,
        level_numbers=range(1, instance.student_class.number + 1),
    )


@receiver(post_save, sender=Level)
def create_standards_when_level_created(sender, instance, created, **kwargs):
    """При добавлении нового уровня необходимо заполняет соответствующие строки в таблице StudentStandard для соответствующих учащихся."""
    if not created:
        return
    students = Student.objects.filter(
        student_class__class_owner=instance.standard.who_added,
        gender=instance.gender,
        student_class__number__gte=instance.level_number,
    )
    _bulk_create_missing_student_standards(
        students=students,
        standards=[instance.standard],
        level_numbers=[instance.level_number],
    )
