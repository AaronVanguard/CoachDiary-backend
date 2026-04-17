from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from standards.models import Level, Standard, StudentStandard
from students.models import Student, StudentClass
from users.models import User


class TeacherDataImportError(ValueError):
    """Raised when the teacher data payload is malformed."""


@dataclass
class ImportSummary:
    classes: int = 0
    students: int = 0
    standards: int = 0
    levels: int = 0
    results: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            'imported_classes': self.classes,
            'imported_students': self.students,
            'imported_standards': self.standards,
            'imported_levels': self.levels,
            'imported_results': self.results,
        }


REQUIRED_TOP_LEVEL_KEYS = ('standards', 'classes')


def import_teacher_data(user: User, data: dict[str, Any]) -> ImportSummary:
    """Import a teacher's classes, standards, levels, and student results from a JSON-ready dict.

    Idempotent: existing entities are matched by natural keys
    (standard name, class (number, class_name), student (first/last/patronymic, class)).
    """
    if not all(k in data for k in REQUIRED_TOP_LEVEL_KEYS):
        raise TeacherDataImportError("Файл не содержит необходимые данные")

    summary = ImportSummary()
    with transaction.atomic():
        standard_id_map = _import_standards_and_levels(user, data['standards'], summary)
        _import_classes_students_results(user, data['classes'], standard_id_map, summary)
    return summary


def _import_standards_and_levels(
    user: User,
    standards_data: list[dict[str, Any]],
    summary: ImportSummary,
) -> dict[int, int]:
    existing_standards = {s.name: s for s in Standard.objects.filter(who_added=user)}
    existing_levels = {
        (lvl.standard_id, lvl.level_number, lvl.gender): lvl
        for lvl in Level.objects.filter(standard__who_added=user)
    }
    standard_id_map: dict[int, int] = {}

    for entry in standards_data:
        standard = existing_standards.get(entry['name'])
        if not standard:
            standard = Standard.objects.create(
                name=entry['name'],
                who_added=user,
                description=entry.get('description', ''),
                has_numeric_value=entry.get('has_numeric_value', True),
            )
            existing_standards[standard.name] = standard
            summary.standards += 1

        standard_id_map[entry['id']] = standard.id

        levels_to_create = []
        for level_data in entry.get('levels', []):
            key = (standard.id, level_data['level_number'], level_data['gender'])
            if key in existing_levels:
                continue
            levels_to_create.append(Level(
                standard=standard,
                level_number=level_data['level_number'],
                gender=level_data['gender'],
                is_lower_better=level_data.get('is_lower_better', False),
                low_value=level_data.get('low_value'),
                middle_value=level_data.get('middle_value'),
                high_value=level_data.get('high_value'),
            ))

        if levels_to_create:
            created = Level.objects.bulk_create(levels_to_create)
            summary.levels += len(created)
            for lvl in created:
                existing_levels[(lvl.standard_id, lvl.level_number, lvl.gender)] = lvl

    return standard_id_map


def _student_key(student_data: dict[str, Any], class_id: int) -> tuple:
    return (
        student_data['first_name'],
        student_data['last_name'],
        student_data.get('patronymic', ''),
        class_id,
    )


def _import_classes_students_results(
    user: User,
    classes_data: list[dict[str, Any]],
    standard_id_map: dict[int, int],
    summary: ImportSummary,
) -> None:
    existing_classes = {
        (c.number, c.class_name): c
        for c in StudentClass.objects.filter(class_owner=user)
    }
    existing_students = {
        (s.first_name, s.last_name, s.patronymic, s.student_class_id): s
        for s in Student.objects.filter(student_class__class_owner=user)
    }
    existing_levels = {
        (lvl.standard_id, lvl.level_number, lvl.gender): lvl
        for lvl in Level.objects.filter(standard__who_added=user)
    }
    existing_results: set[tuple[int, int, datetime.date]] = set(
        StudentStandard.objects
        .filter(student__student_class__class_owner=user)
        .values_list('student_id', 'standard_id', 'date_recorded')
    )

    for class_data in classes_data:
        class_obj = _get_or_create_class(user, class_data, existing_classes, summary)
        _import_students_for_class(class_data, class_obj, existing_students, summary)
        _import_results_for_class(
            class_data, class_obj,
            existing_students, existing_levels, existing_results,
            standard_id_map, summary,
        )


def _get_or_create_class(
    user: User,
    class_data: dict[str, Any],
    existing_classes: dict[tuple[int, str], StudentClass],
    summary: ImportSummary,
) -> StudentClass:
    key = (class_data['number'], class_data['class_name'])
    class_obj = existing_classes.get(key)
    if class_obj:
        return class_obj
    class_obj = StudentClass.objects.create(
        number=class_data['number'],
        class_name=class_data['class_name'],
        class_owner=user,
        is_archived=class_data.get('is_archived', False),
    )
    existing_classes[key] = class_obj
    summary.classes += 1
    return class_obj


def _import_students_for_class(
    class_data: dict[str, Any],
    class_obj: StudentClass,
    existing_students: dict[tuple, Student],
    summary: ImportSummary,
) -> None:
    to_create: list[Student] = []
    pending_keys: list[tuple] = []

    for student_data in class_data.get('students', []):
        key = _student_key(student_data, class_obj.id)
        if key in existing_students:
            continue
        to_create.append(Student(
            first_name=student_data['first_name'],
            last_name=student_data['last_name'],
            patronymic=student_data.get('patronymic', ''),
            student_class=class_obj,
            birthday=datetime.datetime.strptime(student_data['birthday'], '%Y-%m-%d').date(),
            gender=student_data['gender'],
        ))
        pending_keys.append(key)

    if not to_create:
        return

    created = Student.objects.bulk_create(to_create, batch_size=1000)
    summary.students += len(created)
    for key, student in zip(pending_keys, created):
        existing_students[key] = student


def _import_results_for_class(
    class_data: dict[str, Any],
    class_obj: StudentClass,
    existing_students: dict[tuple, Student],
    existing_levels: dict[tuple, Level],
    existing_results: set[tuple[int, int, datetime.date]],
    standard_id_map: dict[int, int],
    summary: ImportSummary,
) -> None:
    results_to_create: list[StudentStandard] = []

    for student_data in class_data.get('students', []):
        student = existing_students.get(_student_key(student_data, class_obj.id))
        if not student:
            continue

        for result_data in student_data.get('standards_results', []):
            standard_id = standard_id_map.get(result_data['standard_id'])
            if not standard_id:
                continue

            date_recorded = datetime.datetime.strptime(result_data['date_recorded'], '%Y-%m-%d').date()
            result_key = (student.id, standard_id, date_recorded)
            if result_key in existing_results:
                continue

            level_number = result_data.get('level_number')
            level = existing_levels.get((standard_id, level_number, student.gender)) if level_number else None

            results_to_create.append(StudentStandard(
                student=student,
                standard_id=standard_id,
                date_recorded=date_recorded,
                level=level,
                value=result_data.get('value'),
                grade=result_data.get('grade'),
            ))
            existing_results.add(result_key)

    if results_to_create:
        StudentStandard.objects.bulk_create(results_to_create, batch_size=1000)
        summary.results += len(results_to_create)
