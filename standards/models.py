import logging

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from common.models import AbstractLevel, AbstractStandard, BaseModel
from users.models import User

logger = logging.getLogger(__name__)


class Standard(AbstractStandard):
    """
    Норматив/стандарт для оценки учеников.
    """
    who_added = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        verbose_name="Кто добавил норматив",
    )

    class Meta:
        verbose_name = "Норматив"
        verbose_name_plural = "Нормативы"

    def get_levels(self):
        return self.levels.all()


class Level(AbstractLevel):
    """
    Уровень норматива для определенного класса.
    """
    standard = models.ForeignKey(
        Standard,
        on_delete=models.CASCADE,
        related_name="levels",
        verbose_name="Норматив",
    )

    def calculate_grade(self, value):
        """
        Рассчитывает оценку на основе полученного значения.
        """
        if value is None:
            return None

        if not self.standard.has_numeric_value:
            return value

        if not self.is_lower_better:
            if value >= self.high_value:
                return 5
            elif value >= self.middle_value:
                return 4
            elif value >= self.low_value:
                return 3
            else:
                return 2
        else:
            if value <= self.high_value:
                return 5
            elif value <= self.middle_value:
                return 4
            elif value <= self.low_value:
                return 3
            else:
                return 2

    def clean(self):
        if self.standard.has_numeric_value:
            if not all([self.low_value, self.middle_value, self.high_value]):
                raise ValidationError(
                    "Для нормативов с числовым значением необходимо указывать все уровневые значения."
                )
        else:
            if any([self.low_value, self.middle_value, self.high_value]):
                raise ValidationError(
                    "Для нормативов без числового значения не указываются уровневые значения."
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Уровень норматива"
        verbose_name_plural = "Уровни нормативов"
        ordering = ['standard', 'level_number']

    def __str__(self):
        return f"Уровень {self.level_number} для {self.standard.name} ({self.get_gender_display()})"


class StudentStandard(BaseModel):
    """
    Результаты выполнения нормативов учениками.
    """
    student = models.ForeignKey(
        'students.Student',
        on_delete=models.CASCADE,
        related_name="standards",
        verbose_name="Ученик",
    )
    standard = models.ForeignKey(
        Standard,
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name="Норматив",
    )
    grade = models.IntegerField(
        verbose_name="Оценка",
        validators=[MinValueValidator(2), MaxValueValidator(5)],
        null=True,
    )
    value = models.FloatField(
        verbose_name="Значение",
        null=True,
    )
    level = models.ForeignKey(
        Level,
        on_delete=models.CASCADE,
        verbose_name="Уровень",
        null=True,
        blank=True,
    )
    date_recorded = models.DateField(
        default=timezone.now,
        verbose_name="Дата записи"
    )

    GRADE_RECOMPUTE_FIELDS = frozenset({
        'value', 'level', 'level_id', 'student', 'student_id', 'standard', 'standard_id',
    })

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and not self.GRADE_RECOMPUTE_FIELDS.intersection(update_fields):
            super().save(*args, **kwargs)
            return

        if self.level_id is None:
            try:
                self.level = Level.objects.get(
                    standard=self.standard,
                    level_number=self.student.student_class.number,
                    gender=self.student.gender,
                )
            except Level.DoesNotExist:
                logger.warning(
                    "Level not found for standard '%s', class %s, gender '%s'.",
                    self.standard.name, self.student.student_class.number, self.student.get_gender_display(),
                )

        self.grade = self.level.calculate_grade(self.value) if self.level_id else None
        if isinstance(self.grade, float):
            self.grade = round(self.grade)
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.student.full_name} - {self.standard.name}: "
            f"{self.value} ({self.grade})"
        )

    class Meta:
        verbose_name = "Результат ученика"
        verbose_name_plural = "Результаты учеников"
        ordering = ['-date_recorded', 'student']
