from __future__ import annotations

from typing import Any

from standards.models import Standard, StudentStandard
from students.models import Student, StudentClass
from users.models import User


def build_teacher_export(user: User) -> dict[str, Any]:
    """Serialize a teacher's classes, standards, levels, and student results to a JSON-ready dict."""
    standards = Standard.objects.filter(who_added=user).prefetch_related('levels')
    classes = StudentClass.objects.filter(class_owner=user).prefetch_related('students')

    results_by_student: dict[int, list[StudentStandard]] = {}
    owned_students = StudentStandard.objects.filter(
        student__student_class__class_owner=user,
    ).select_related('standard', 'level')
    for result in owned_students:
        results_by_student.setdefault(result.student_id, []).append(result)

    return {
        'standards': [_serialize_standard(s) for s in standards],
        'classes': [_serialize_class(c, results_by_student) for c in classes],
    }


def _serialize_standard(standard: Standard) -> dict[str, Any]:
    return {
        'id': standard.id,
        'name': standard.name,
        'description': standard.description,
        'has_numeric_value': standard.has_numeric_value,
        'levels': [
            {
                'id': level.id,
                'level_number': level.level_number,
                'is_lower_better': level.is_lower_better,
                'gender': level.gender,
                'low_value': level.low_value,
                'middle_value': level.middle_value,
                'high_value': level.high_value,
            }
            for level in standard.levels.all()
        ],
    }


def _serialize_class(
    student_class: StudentClass,
    results_by_student: dict[int, list[StudentStandard]],
) -> dict[str, Any]:
    return {
        'number': student_class.number,
        'class_name': student_class.class_name,
        'is_archived': student_class.is_archived,
        'students': [
            _serialize_student(s, results_by_student.get(s.id, []))
            for s in student_class.students.all()
        ],
    }


def _serialize_student(student: Student, results: list[StudentStandard]) -> dict[str, Any]:
    return {
        'first_name': student.first_name,
        'last_name': student.last_name,
        'patronymic': student.patronymic,
        'birthday': student.birthday.strftime('%Y-%m-%d'),
        'gender': student.gender,
        'standards_results': [
            {
                'standard_id': r.standard.id,
                'standard_name': r.standard.name,
                'value': r.value,
                'grade': r.grade,
                'level_id': r.level.id if r.level else None,
                'level_number': r.level.level_number if r.level else None,
                'date_recorded': r.date_recorded.strftime('%Y-%m-%d'),
            }
            for r in results
        ],
    }
