import json
import logging
import uuid
from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.http import HttpResponse
from django.middleware.csrf import get_token
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import response, status, viewsets, mixins, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.utils import timezone

from common.permissions import IsTeacher
from students.api.serializers import InvitationDetailSerializer
from students.models import Invitation
from users import models
from users.api.serializers import UserSerializer, UserCreateSerializer, ChangePasswordSerializer, \
    ChangeUserDetailsSerializer, ChangeUserEmailSerializer, UserLoginSerializer, UserInvitationSerializer, \
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from users.services.teacher_export import build_teacher_export
from users.services.teacher_import import TeacherDataImportError, import_teacher_data
from users.services.teacher_xlsx import XlsxOptions, build_teacher_xlsx
from users.signals import send_confirmation_email
from users.utils import send_password_reset_email

logger = logging.getLogger(__name__)


class UserLoginView(viewsets.ViewSet):
    serializer_class = UserLoginSerializer

    @extend_schema(
        summary="Получение CSRF токена",
        description="Получение CSRF токена для защиты от CSRF атак"
    )
    def list(self, request):
        return response.Response({"csrf": get_token(request)})

    @extend_schema(
        summary="Вход пользователя (сессия браузера)",
        description="Вход пользователя в систему"
    )
    def create(self, request):
        """ Вход пользователя. """
        email = request.data.get("email")
        password = request.data.get("password")

        self._validate_email_and_password(email, password)

        user = authenticate(request, email=email, password=password)
        if user is None or not user.is_authenticated:
            return response.Response(
                {
                    "status": "ошибка",
                    "details": "Не правильно указана почта или пароль.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login(request, user)
        return response.Response(
            {
                "status": "успешно",
                "details": "Вход выполнен.",
            }
        )

    def _validate_email_and_password(self, email: str, password: str):
        """ Проверка, указаны ли эл. почта и пароль."""
        if not email and password:
            raise ValidationError("Нужно заполнить поле эл. почты.")
        if email and not password:
            raise ValidationError("Нужно заполнить поле пароля.")
        if not email and not password:
            raise ValidationError(
                "Нужно указать почту и пароль.",
            )


class UserViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = UserCreateSerializer

    @extend_schema(
        summary="Регистрация",
        description="Создание нового пользователя"
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            'message': 'Регистрация успешна. Проверьте вашу почту для подтверждения аккаунта.'
        }, status=status.HTTP_201_CREATED)


class UserProfileViewSet(viewsets.GenericViewSet):
    """ Работа с профилем пользователя. """
    serializer_class = UserSerializer
    permission_classes = (
        permissions.IsAuthenticated,
    )

    @extend_schema(
        summary="Просмотр профиля пользователя"
    )
    def list(self, request):
        user = request.user
        serializer = UserSerializer(user)
        return response.Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Смена пароля пользователя",
        request=ChangePasswordSerializer,
    )
    @action(detail=False, methods=['patch'])
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data['new_password'])
        user.save()
        return response.Response({"success": "Пароль успешно установлен"}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Смена ФИО",
        request=ChangeUserDetailsSerializer,
        responses={200: UserSerializer},
    )
    @action(detail=False, methods=['patch'])
    def change_details(self, request):
        serializer = ChangeUserDetailsSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        for field in ('first_name', 'last_name', 'patronymic'):
            if field in serializer.validated_data:
                setattr(user, field, serializer.validated_data[field])
        user.save()
        return response.Response({"success": "Данные пользователя успешно обновлены"}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Cмена эл. почты",
        request=ChangeUserEmailSerializer,
        responses={200: UserSerializer},
    )
    @action(detail=False, methods=['patch'])
    def change_email(self, request):
        serializer = ChangeUserEmailSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        new_email = serializer.validated_data.get('email')
        if new_email and new_email != user.email:
            user.email = new_email
            user.save()
        return response.Response({"success": "Данные пользователя успешно обновлены"}, status=status.HTTP_200_OK)


class UserLogoutView(viewsets.ViewSet):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = None

    @extend_schema(
        summary="Выполнение выхода из аккаунта"
    )
    def create(self, request):
        logout(request)
        return response.Response(
            {
                "status": "success",
                "detail": "Вы вышли из аккаунта",
            },
            status=status.HTTP_200_OK
        )


class ResendConformationEmailView(viewsets.ViewSet):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = None

    @extend_schema(
        summary="Отправка повторного письма для подтверждения эл. почты",
        description="Отправляет повторное письмо для подтверждения эл. почты"
    )
    def list(self, request):
        user = request.user
        if user.is_email_verified:
            return Response(
                {"error": "Эл. почта уже подтверждена"},
                status=status.HTTP_400_BAD_REQUEST
            )

        send_confirmation_email(instance=user, created=True, sender=user)
        return Response(
            {"success": "Инструкции по подтверждению эл. почты отправлены на указанный адрес электронной почты"},
            status=status.HTTP_200_OK
        )


class PasswordResetViewSet(viewsets.ViewSet):
    permission_classes = (permissions.AllowAny,)

    @extend_schema(
        summary="Запрос на сброс пароля",
        request=PasswordResetRequestSerializer
    )
    @action(detail=False, methods=['post'])
    def request_reset(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = models.User.objects.filter(email=email).first()

        if not user:
            # Для безопасности не сообщаем, что пользователя не существует
            return Response(
                {
                    "success": "Если данная почта существует, инструкции по сбросу пароля отправлены на указанный адрес электронной почты"},
                status=status.HTTP_200_OK
            )

        reset_token = uuid.uuid4()

        user.password_reset_token = reset_token
        user.password_reset_expires = timezone.datetime.now(tz=timezone.timezone.utc) + timedelta(hours=24)
        user.save()
        send_password_reset_email(user)

        return Response(
            {
                "success": "Если данная почта существует, инструкции по сбросу пароля отправлены на указанный адрес электронной почты"},
            status=status.HTTP_200_OK
        )

    @extend_schema(
        summary="Установка нового пароля",
        request=PasswordResetConfirmSerializer,
        description="Установка нового пароля по токену сброса пароля"
    )
    @action(detail=False, methods=['post'])
    def confirm_reset(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        user = models.User.objects.filter(password_reset_token=token, password_reset_expires__gt=timezone.datetime.now(
            tz=timezone.timezone.utc)).first()

        if user is None:
            return Response(
                {"error": "Недействительный или устаревший токен сброса пароля"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)

        user.password_reset_token = None
        user.password_reset_expires = None
        user.save()

        return Response(
            {"success": "Пароль успешно изменен. Теперь вы можете войти в систему"},
            status=status.HTTP_200_OK
        )


class JoinByInvitationView(mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, viewsets.ViewSet):
    queryset = Invitation.objects.all()
    serializer_class = UserInvitationSerializer
    lookup_field = 'invite_code'

    @extend_schema(
        summary="Получение информации о студенте по коду приглашения",
        description="Получение информации о студенте по коду приглашения",
        responses={200: InvitationDetailSerializer}
    )
    def retrieve(self, request, *args, **kwargs):
        code = kwargs.get('invite_code')
        invitation = Invitation.objects.get(invite_code=code)
        if invitation.is_used:
            return Response(
                {"error": "Это приглашение уже использовано."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = InvitationDetailSerializer(invitation)
        return Response(serializer.data)

    @extend_schema(
        summary="Регистрация по коду приглашения",
        description="Регистрация ученика в качестве пользователя по коду приглашения",
        request=UserInvitationSerializer,
        responses={201: UserCreateSerializer}
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = UserInvitationSerializer(data=request.data)
        if serializer.is_valid():
            invite_code = serializer.validated_data.pop('invite_code')
            invitation = get_object_or_404(Invitation, invite_code=invite_code, is_used=False)
            student = invitation.student

            user = serializer.save()

            student.user = user
            user.role = 'student'
            user.save()
            student.save()
            invitation.is_used = True
            invitation.save()

            return Response(
                {
                    "success": "Регистрация успешна. Проверьте вашу почту для подтверждения аккаунта."
                },
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class VerifyEmailView(viewsets.ViewSet):
    permission_classes = (permissions.AllowAny,)

    @extend_schema(
        summary="Подтверждение эл. почты",
        description="Подтверждение эл. почты по токену",
    )
    def list(self, request, token):
        user = models.User.objects.filter(verification_token=token).first()
        if user is None:
            return Response({'error': 'Неверный токен подтверждения'}, status=status.HTTP_400_BAD_REQUEST)

        user.verification_token = None
        user.is_email_verified = True

        user.save()
        return Response({'message': 'Email успешно подтвержден'}, status=status.HTTP_200_OK)


class TeacherImportExportViewSet(viewsets.ViewSet):
    permission_classes = (IsTeacher,)

    @extend_schema(
        summary="Экспортирует данные преподавателя в формате JSON",
        description="Включает информацию о классах и учениках, которыми управляет преподаватель",
    )
    @action(detail=False, methods=['get'])
    def export_data(self, request):
        user = request.user
        payload = build_teacher_export(user)
        http_response = HttpResponse(
            json.dumps(payload, ensure_ascii=False, indent=4),
            content_type='application/json',
        )
        http_response['Content-Disposition'] = f'attachment; filename="teacher_data_export_{user.id}.json"'
        return http_response

    @extend_schema(
        summary="Импортирует данные преподавателя из JSON-файла",
        description="Позволяет восстановить данные о классах, учениках, нормативах и их результатах",
    )
    @action(detail=False, methods=['post'])
    def import_data(self, request):
        uploaded_file = request.FILES.get('file')
        if uploaded_file is None:
            return Response({'error': 'Файл не был предоставлен'}, status=status.HTTP_400_BAD_REQUEST)
        if not uploaded_file.name.endswith('.json'):
            return Response({'error': 'Файл должен быть в формате JSON'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(uploaded_file.read().decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({'error': 'Неверный формат JSON-файла'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            summary = import_teacher_data(request.user, payload)
        except TeacherDataImportError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (KeyError, ValueError) as exc:
            logger.exception("Malformed teacher data import for user %s", request.user.id)
            return Response(
                {'error': f'Некорректные данные в файле: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'success': True,
            'message': (
                f'Импорт завершен. Добавлено: классов: {summary.classes}, '
                f'учеников: {summary.students}, стандартов: {summary.standards}, '
                f'уровней: {summary.levels}, результатов: {summary.results}'
            ),
            **summary.as_dict(),
        })

    @extend_schema(
        summary="Экспорт данных в формате XLSX",
        description="Создает Excel-файл с данными по нормативам для всех учеников",
        parameters=[
            OpenApiParameter(
                name='include_norms', type=OpenApiTypes.BOOL, location='query', required=False,
                description='Включать лист с таблицей нормативов', default=True,
            ),
            OpenApiParameter(
                name='include_results', type=OpenApiTypes.BOOL, location='query', required=False,
                description='Включать сводный лист с итоговыми результатами', default=True,
            ),
            OpenApiParameter(
                name='include_standards', type=OpenApiTypes.BOOL, location='query', required=False,
                description='Включать отдельные листы для каждого норматива', default=True,
            ),
        ],
    )
    @action(detail=False, methods=['get'])
    def export_xlsx(self, request):
        def _flag(name: str) -> bool:
            return request.query_params.get(name, 'true').lower() == 'true'

        xlsx_bytes = build_teacher_xlsx(
            request.user,
            XlsxOptions(
                include_norms=_flag('include_norms'),
                include_results=_flag('include_results'),
                include_standards=_flag('include_standards'),
            ),
        )
        http_response = HttpResponse(
            xlsx_bytes,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        http_response['Content-Disposition'] = 'attachment; filename="standards_export.xlsx"'
        return http_response
