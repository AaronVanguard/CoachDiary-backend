from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from common.excel_utils import create_excel_formats, write_headers_and_data
from standards.models import Level, Standard, StudentStandard
from students.models import Student, StudentClass
from users.models import User

CLASS_NUMBERS = range(1, 12)
GENDER_LABELS = {'m': 'м', 'f': 'ж'}
STUDENT_HEADER_COLUMNS = ["№", "ФИО", "Пол", "Класс", "Дата рождения"]


@dataclass(frozen=True)
class XlsxOptions:
    include_norms: bool = True
    include_results: bool = True
    include_standards: bool = True


def build_teacher_xlsx(user: User, options: XlsxOptions) -> bytes:
    standards = list(Standard.objects.filter(who_added=user))
    classes = list(StudentClass.objects.filter(class_owner=user))

    output = io.BytesIO()
    with pd.ExcelWriter(
        output,
        engine='xlsxwriter',
        engine_kwargs={"options": {"nan_inf_to_errors": True}},
    ) as writer:
        formats = create_excel_formats(writer.book)

        if options.include_norms:
            _write_norms_table(writer, standards, formats)
        if options.include_results:
            _write_results_sheet(writer, standards, classes, formats)
        if options.include_standards:
            for standard in standards:
                _write_standard_sheet(writer, standard, classes, formats)

    output.seek(0)
    return output.read()


def _sanitize(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace([float('inf'), -float('inf'), pd.NA, pd.NaT], None)


def _students_for_classes(classes: Iterable[StudentClass]) -> list[Student]:
    return list(
        Student.objects
        .filter(student_class__in=classes)
        .select_related('student_class')
        .order_by('student_class__number', 'student_class__class_name', 'last_name', 'first_name')
    )


def _student_row_header(index: int, student: Student) -> list:
    return [
        index,
        student.full_name,
        GENDER_LABELS.get(student.gender, ''),
        f"{student.student_class.number}{student.student_class.class_name}",
        student.birthday.strftime("%d.%m.%Y"),
    ]


def _write_norms_table(writer, standards: list[Standard], formats) -> None:
    columns = ["норматив"]
    for class_num in CLASS_NUMBERS:
        for gender_label in ('мальчики', 'девочки'):
            for level_label in ('повышенный', 'высокий', 'средний'):
                columns.append(f"{class_num} класс {gender_label} {level_label}")

    numeric_standards = [s for s in standards if s.has_numeric_value]
    levels_by_key = {
        (lvl.standard_id, lvl.level_number, lvl.gender): lvl
        for lvl in Level.objects.filter(standard__in=numeric_standards)
    }

    rows = []
    for standard in numeric_standards:
        row = [standard.name]
        for class_num in CLASS_NUMBERS:
            for gender in ('m', 'f'):
                lvl = levels_by_key.get((standard.id, class_num, gender))
                if lvl:
                    row.extend([lvl.high_value, lvl.middle_value, lvl.low_value])
                else:
                    row.extend([None, None, None])
        rows.append(row)

    df = _sanitize(pd.DataFrame(rows, columns=columns))
    sheet_name = "Таблица нормативов"
    df.to_excel(writer, sheet_name=sheet_name, index=False)

    worksheet = writer.sheets[sheet_name]
    worksheet.set_column(0, 0, 30)
    worksheet.set_column(1, len(columns), 12)
    write_headers_and_data(worksheet, df, formats)


def _write_results_sheet(
    writer,
    standards: list[Standard],
    classes: list[StudentClass],
    formats,
) -> None:
    students = _students_for_classes(classes)
    student_ids = [s.id for s in students]

    latest_by_level: dict[tuple[int, int, int], StudentStandard] = {}
    qs = (
        StudentStandard.objects
        .filter(student_id__in=student_ids, standard__in=standards)
        .select_related('level', 'standard')
        .order_by('-date_recorded')
    )
    for result in qs:
        if not result.level:
            continue
        key = (result.student_id, result.standard_id, result.level.level_number)
        latest_by_level.setdefault(key, result)

    columns = STUDENT_HEADER_COLUMNS + [f"Итого {n} класс" for n in CLASS_NUMBERS]
    rows = []
    for idx, student in enumerate(students, 1):
        row = _student_row_header(idx, student)
        for class_num in CLASS_NUMBERS:
            grades = [
                latest_by_level[(student.id, s.id, class_num)].grade
                for s in standards
                if (student.id, s.id, class_num) in latest_by_level
                and latest_by_level[(student.id, s.id, class_num)].grade is not None
            ]
            row.append(round(sum(grades) / len(grades), 1) if grades else '')
        rows.append(row)

    df = _sanitize(pd.DataFrame(rows, columns=columns))
    sheet_name = "Итоговые результаты"
    df.to_excel(writer, sheet_name=sheet_name, index=False)

    worksheet = writer.sheets[sheet_name]
    worksheet.set_column(0, 0, 5)
    worksheet.set_column(1, 1, 25)
    worksheet.set_column(2, 2, 6)
    worksheet.set_column(3, 3, 8)
    worksheet.set_column(4, 4, 15)
    worksheet.set_column(5, len(columns), 15)
    write_headers_and_data(worksheet, df, formats, gender_col=2)


def _write_standard_sheet(
    writer,
    standard: Standard,
    classes: list[StudentClass],
    formats,
) -> None:
    students = _students_for_classes(classes)
    student_ids = [s.id for s in students]

    results_by_student: dict[int, dict[int, StudentStandard]] = {}
    qs = (
        StudentStandard.objects
        .filter(student_id__in=student_ids, standard=standard)
        .select_related('level')
        .order_by('level__level_number')
    )
    for result in qs:
        if not result.level:
            continue
        results_by_student.setdefault(result.student_id, {})[result.level.level_number] = result

    columns = list(STUDENT_HEADER_COLUMNS)
    columns[2] = "пол"
    columns[3] = "класс"
    columns[4] = "д.р."
    for class_num in CLASS_NUMBERS:
        columns.extend([str(class_num), f"{class_num}ур"])

    rows = []
    for idx, student in enumerate(students, 1):
        row = _student_row_header(idx, student)
        student_results = results_by_student.get(student.id, {})
        for class_num in CLASS_NUMBERS:
            result = student_results.get(class_num)
            if result:
                value = result.value if result.value not in (float('inf'), -float('inf')) else None
                row.extend([value, result.grade])
            else:
                row.extend([0, ''])
        rows.append(row)

    df = _sanitize(pd.DataFrame(rows, columns=columns))
    sheet_name = standard.name[:31]
    df.to_excel(writer, sheet_name=sheet_name, index=False)

    worksheet = writer.sheets[sheet_name]
    worksheet.set_column(0, 0, 5)
    worksheet.set_column(1, 1, 25)
    worksheet.set_column(2, len(columns), 10)
    write_headers_and_data(worksheet, df, formats, gender_col=2)
