import base64
from io import BytesIO

import qrcode
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django_filters import rest_framework as filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter, OpenApiResponse
from rest_framework import mixins, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from xhtml2pdf import pisa

from common import utils
from common.permissions import IsTeacher
from . import filters as custom_filters
from . import serializers
from .. import models


def _build_qr_code_base64(data: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


@extend_schema_view(
    list=extend_schema(
        summary="Получение списка студентов",
        description="Возвращает список студентов, доступных для текущего пользователя. "
                    "Учителя видят всех студентов в своих классах, студенты видят только себя.",
    ),
    retrieve=extend_schema(
        summary="Получение информации о студенте",
        description="Возвращает информацию о конкретном студенте. "
                    "Учителя могут видеть информацию о студентах в своих классах, "
                    "студенты видят только себя.",
    ),
    create=extend_schema(
        summary="Создание нового студента",
        description="Создаёт нового студента в базе данных. "
                    "Учителя могут создавать студентов в своих классах, "
                    "студенты не могут создавать себя.",
    ),
    update=extend_schema(
        summary="Обновление информации о студенте",
        description="Обновляет информацию о студенте.",
    ),
    partial_update=extend_schema(
        summary="Частичное обновление информации о студенте",
        description="Частично обновляет информацию о студенте. "
                    "Позволяет обновлять только некоторые поля.",
    ),
    destroy=extend_schema(
        summary="Удаление студента",
        description="Удаляет студента из базы данных. "
                    "Учителя могут удалять студентов в своих классах, "
                    "студенты не могут удалять себя. "
                    "Если у студента был аккаунт, то он всё равно сможет входить в систему "
                    "и смотреть свои данные и результаты на момент удаления.",
    ),
)
class StudentViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = serializers.StudentSerializer
    queryset = models.Student.objects.all()
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = custom_filters.StudentFilter
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, 'role', None)
        if role == 'teacher':
            return models.Student.objects.filter(
                student_class__class_owner=user,
            ).select_related('student_class', 'invitation')
        if role == 'student' and getattr(user, 'student', None):
            return models.Student.global_objects.filter(id=user.student.id).select_related(
                'student_class', 'invitation',
            )
        return models.Student.objects.none()

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        user = request.user
        role = getattr(user, 'role', None)
        if role == 'teacher' and obj.student_class.class_owner != user:
            raise PermissionDenied("У вас нет доступа к этому студенту")
        if role == 'student':
            own_student = getattr(user, 'student', None)
            if not own_student or own_student.id != obj.id:
                raise PermissionDenied("У вас нет доступа к этому студенту")

    @extend_schema(
        summary="Генерация PDF с QR-кодами для студентов класса",
        description="Генерирует PDF-файл, содержащий QR-коды для каждого студента в классе. "
                    "QR-коды содержат ссылки на приглашения для регистрации в роли обучающегося.",
        parameters=[
            OpenApiParameter(
                name='class_id',
                required=True,
                type=OpenApiTypes.INT,
                description="ID класса, для которого нужно сгенерировать QR-коды.")
        ],
        responses={200: OpenApiResponse(response=OpenApiTypes.BINARY)},
    )
    @action(detail=False, methods=['get'])
    def generate_qr_codes_pdf(self, request):
        class_id = request.query_params.get('class_id')
        if not class_id:
            return Response(
                {"error": "Необходимо указать параметр class_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student_class = get_object_or_404(
            models.StudentClass, id=class_id, class_owner=request.user,
        )

        students = (
            models.Student.objects
            .filter(student_class=student_class)
            .select_related('invitation')
        )

        qr_data = []
        for student in students:
            invitation = getattr(student, 'invitation', None)
            if not invitation or invitation.is_used:
                continue
            invitation_link = invitation.get_join_link()
            qr_data.append({
                'invite_code': invitation.invite_code,
                'initials': student.initials,
                'invitation_link': invitation_link,
                'qr_code': _build_qr_code_base64(invitation_link),
            })

        html_string = render_to_string('qr_codes_template.html', {
            'student_class': student_class,
            'qr_data': qr_data,
        })

        result = BytesIO()
        pisa_status = pisa.CreatePDF(
            html_string,
            dest=result,
            encoding='UTF-8',
            link_callback=utils.link_callback,
        )
        if pisa_status.err:
            return Response(
                {"error": f"Ошибка при создании PDF: {pisa_status.err}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        result.seek(0)
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="qr_codes_class_{class_id}.pdf"'
        return response


@extend_schema_view(
    list=extend_schema(
        summary="Получение списка классов",
        description="Возвращает список классов, доступных для текущего пользователя. "
                    "Учителя видят только свои классы.",
    ),
    retrieve=extend_schema(
        summary="Получение информации о классе",
        description="Возвращает информацию о конкретном классе. "
                    "Учителя могут видеть информацию только о своих классах.",
    ),
    destroy=extend_schema(
        summary="Удаление класса",
        description="Удаляет класс из базы данных вместе с его студентами. "
                    "Учителя могут удалять только свои классы.",
    ),
)
class StudentClassViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = serializers.StudentClassSerializer
    permission_classes = (IsTeacher,)
    queryset = models.StudentClass.objects.all()

    def get_queryset(self):
        return models.StudentClass.objects.filter(class_owner=self.request.user)

    @extend_schema(
        summary="Переводит все классы на следующий год обучения",
        description="Переводит все классы текущего пользователя на следующий год обучения. "
                    "Если класс 11, то он удаляется.",
        request=None,
    )
    @action(detail=False, methods=['post'])
    def promote(self, request, *args, **kwargs):
        user = request.user
        models.StudentClass.objects.filter(class_owner=user, number=11).delete()
        for student_class in models.StudentClass.objects.filter(class_owner=user):
            student_class.number += 1
            student_class.save()

        serializer = serializers.StudentClassSerializer(
            models.StudentClass.objects.filter(class_owner=user), many=True,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
