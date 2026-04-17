from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from common.permissions import IsTeacher, IsTeacherOrStudent
from standards import models
from students.models import Student
from .serializers import StudentResultSerializer, StandardSerializer, StudentStandardCreateSerializer, \
    StudentStandardsResponseSerializer


class StandardValueViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StandardSerializer
    permission_classes = (
        IsTeacher,
    )

    @extend_schema(
        summary="Список всех нормативов текущего пользователя",
        description="Отображает список всех нормативов, добавленных текущим пользователем."
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Получение выбранного норматива",
        description="Отображает информацию о выбранном нормативе по его ID."
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="Полное обновление норматива",
        description="Обновляет все поля норматива, включая уровни и их значения."
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(
        summary="Частичное обновление норматива",
        description="Обновляет только указанные поля норматива. Уровни не обновляются, если не указаны."
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        summary="Удаление норматива",
        description="Удаляет норматив по его ID. Удаление уровней норматива не предусмотрено в этом методе."
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        summary="Создание нового норматива",
        description="Создает новый норматив. Если норматив с таким именем уже существует, добавляет к нему новые уровни вместо создания нового."
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary="Удаление уровней норматива по номеру класса",
        description="Удаляет уровни норматива для указанного номера класса (для обоих полов)",
        parameters=[
            OpenApiParameter(
                name='level_number',
                type=OpenApiTypes.INT,
                location='query',
                description='Номер уровня (класса), для которого нужно удалить норматив',
                required=True
            ),
        ]
    )
    @action(detail=True, methods=['delete'])
    def remove_level(self, request, pk=None):
        standard = self.get_object()
        level_number = request.query_params.get('level_number')

        if not level_number:
            return Response(
                {"detail": "Необходимо указать параметр level_number"},
                status=status.HTTP_400_BAD_REQUEST
            )

        levels_to_delete = standard.levels.filter(level_number=level_number)

        if levels_to_delete.exists():
            deleted_count = levels_to_delete.count()
            levels_to_delete.delete()
            return Response(
                {"detail": f"Удалено уровней: {deleted_count}"},
                status=status.HTTP_204_NO_CONTENT
            )
        else:
            return Response(
                {"detail": f"Уровни с номером {level_number} не найдены для данного норматива"},
                status=status.HTTP_404_NOT_FOUND
            )

    def get_queryset(self):
        user = self.request.user
        return models.Standard.objects.filter(who_added_id=user.id)

    def perform_create(self, serializer):
        standard_data = serializer.validated_data
        standard_name = standard_data.get('name')

        existing_standard = models.Standard.objects.filter(name=standard_name).first()

        if existing_standard:
            levels_data = standard_data.get('levels', [])

            for level_data in levels_data:
                level_exists = models.Level.objects.filter(
                    standard=existing_standard,
                    level_number=level_data.get('level_number'),
                    gender=level_data.get('gender')
                ).exists()

                if not level_exists:
                    models.Level.objects.create(
                        standard=existing_standard,
                        **level_data
                    )

            return existing_standard
        else:
            serializer.save(who_added_id=self.request.user.id)

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save()


class StudentStandardsViewSet(viewsets.ViewSet):
    serializer_class = StudentStandardsResponseSerializer
    permission_classes = (IsTeacherOrStudent,)

    @extend_schema(
        summary="Результаты ученика по его нормативам",
        description="Отображает результаты ученика по его нормативам",
        parameters=[
            OpenApiParameter(
                name='level_number',
                type=OpenApiTypes.INT,
                location='query',
                description='Номер уровня (класса), для которого нужно вывести оценки. По умолчанию - текущий класс ученика',
                required=False
            ),
        ]
    )
    def list(self, request, student_id=None):
        user = request.user
        if user.role == 'teacher':
            student = Student.objects.filter(id=student_id, student_class__class_owner=user).first()
            if not student:
                raise PermissionDenied("У вас нет прав доступа к этому студенту.")
            student_standards = models.StudentStandard.objects.filter(student=student)
        else:
            own_student = getattr(user, 'student', None)
            if not own_student or str(own_student.id) != str(student_id):
                raise PermissionDenied("У вас нет прав доступа к стандартам этого студента.")
            student = Student.global_objects.filter(id=own_student.id).first()
            if not student:
                raise PermissionDenied("Студент не найден.")
            manager = models.StudentStandard.global_objects if getattr(student, 'is_deleted', False) \
                else models.StudentStandard.objects
            student_standards = manager.filter(student=student)

        level_number_raw = request.query_params.get('level_number')
        if level_number_raw:
            try:
                level_number = int(level_number_raw)
            except ValueError:
                return Response(
                    {"detail": "Параметр level_number должен быть числом"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            level_number = student.student_class.number

        filtered_standards = list(
            student_standards.filter(level__level_number=level_number).select_related('standard', 'level')
        )
        grades = [s.grade for s in filtered_standards if s.grade is not None]
        summary_grade = sum(grades) / len(grades) if grades else 0

        serializer = StudentStandardsResponseSerializer({
            'standards': filtered_standards,
            'summary_grade': summary_grade,
            'level_number': level_number,
        })
        return Response(serializer.data)


class StudentsResultsViewSet(mixins.ListModelMixin, viewsets.ViewSet):
    permission_classes = (IsTeacher,)
    serializer_class = StudentResultSerializer

    @extend_schema(
        summary="Результаты учеников из выбранных классов по выбранным нормативам",
        description="Отображает результаты учеников из выбранного одного или нескольких классов по выбранным нормативам",
        parameters=[
            OpenApiParameter(
                name='class_id[]',
                type=OpenApiTypes.INT,
                location='query',
                description='Идентификаторы классов, для которых нужно вывести результаты учеников',
                required=True
            ),
            OpenApiParameter(
                name='standard_id[]',
                type=OpenApiTypes.INT,
                location='query',
                description='Идентификаторы нормативов, по которым нужно вывести результаты',
                required=True
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        class_ids = request.query_params.getlist('class_id[]')
        standard_ids = request.query_params.getlist('standard_id[]')

        if not class_ids or not standard_ids:
            return Response({"detail": "Требуются параметры class_id[] и standard_id[]."},
                            status=status.HTTP_400_BAD_REQUEST)

        students = Student.objects.filter(
            student_class__id__in=class_ids,
            student_class__class_owner=request.user
        )
        serializer = StudentResultSerializer(
            students,
            many=True,
            context={'standard_ids': standard_ids}
        )

        return Response(serializer.data)


class StudentResultsCreateOrUpdateViewSet(viewsets.ViewSet):
    permission_classes = (IsTeacher,)
    serializer_class = StudentStandardCreateSerializer
    @extend_schema(
        summary="Создание или обновление результатов студентов по нормативам",
        description="Создает или обновляет результаты студентов по нормативам. "
                    "Ожидается список объектов с полями student_id, standard_id, value, grade и level_number.",
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        data = request.data

        if not isinstance(data, list):
            return Response({"error": "Ожидается список объектов."}, status=status.HTTP_400_BAD_REQUEST)

        response_data = []
        errors = []

        to_update = []

        for entry in data:
            serializer = StudentStandardCreateSerializer(data=entry)
            if serializer.is_valid():
                validated_data = serializer.validated_data
                student = validated_data['student']
                student_id = student.id
                standard_id = validated_data['standard'].id
                level_number = validated_data.get('level_number', student.student_class.number)

                try:
                    student_result = models.StudentStandard.objects.select_related('level').get(
                        student_id=student_id, standard_id=standard_id,
                        level__level_number=level_number
                    )

                    has_changes = False
                    changed_fields = []

                    if 'value' in validated_data and validated_data['value'] != student_result.value:
                        has_changes = True
                        changed_fields.append('value')

                    if has_changes:
                        for field in changed_fields:
                            setattr(student_result, field, validated_data[field])
                        to_update.append(student_result)
                        response_data.append({
                            "detail": f"Запись результата студента успешно обновлена. Изменены поля: {', '.join(changed_fields)}",
                            "data": StudentStandardCreateSerializer(instance=student_result).data
                        })
                except models.StudentStandard.DoesNotExist:
                    student_result = serializer.save()
                    response_data.append({
                        "detail": "Запись результата студента успешно создана.",
                        "data": StudentStandardCreateSerializer(instance=student_result).data
                    })
            else:
                errors.append(serializer.errors)

        if to_update:
            for student_result in to_update:
                student_result.save()

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(response_data, status=status.HTTP_200_OK)
