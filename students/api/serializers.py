from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .. import models


class StudentClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.StudentClass
        fields = ("id", "class_name", "number", "recruitment_year")

    def create(self, validated_data):
        request_user = self.context['request'].user
        student_class, created = models.StudentClass.objects.get_or_create(
            number=validated_data['number'],
            class_name=validated_data['class_name'],
            defaults={'class_owner': request_user}
        )

        return student_class

    def update(self, instance, validated_data):
        request_user = self.context['request'].user

        if instance.class_owner != request_user:
            raise ValidationError("Только куратор класса может обновлять данные этого класса.")

        instance.number = validated_data.get('number', instance.number)
        instance.class_name = validated_data.get('class_name', instance.class_name)

        instance.class_owner = request_user

        instance.save()
        return instance


class FullClassNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.StudentClass
        fields = ('id', 'number', 'class_name')


class StudentSerializer(serializers.ModelSerializer):
    student_class = StudentClassSerializer()
    invitation_link = serializers.SerializerMethodField()
    is_used_invitation = serializers.SerializerMethodField()

    class Meta:
        model = models.Student
        fields = ("id", "first_name", "last_name", "patronymic", "full_name", "student_class", "birthday", "gender",
                  "invitation_link", "is_used_invitation")

    def get_invitation_link(self, obj):
        invitation = getattr(obj, 'invitation', None)
        return invitation.get_join_link() if invitation else None

    def get_is_used_invitation(self, obj):
        invitation = getattr(obj, 'invitation', None)
        return bool(invitation and invitation.is_used)

    def create(self, validated_data):
        student_class_data = validated_data.pop('student_class')
        request_user = self.context['request'].user

        class_instance = self.get_or_create_class(student_class_data, request_user)

        student = models.Student.objects.create(student_class=class_instance, **validated_data)
        return student

    def update(self, instance, validated_data):
        student_class_data = validated_data.pop('student_class', None)
        request_user = self.context['request'].user

        if student_class_data:
            class_instance = self.get_or_create_class(student_class_data, request_user)
            instance.student_class = class_instance

        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.patronymic = validated_data.get('patronymic', instance.patronymic)

        instance.birthday = validated_data.get('birthday', instance.birthday)
        instance.gender = validated_data.get('gender', instance.gender)
        instance.save()

        return instance

    def get_or_create_class(self, student_class_data, request_user):
        class_instance, _ = models.StudentClass.objects.get_or_create(
            number=student_class_data['number'],
            class_name=student_class_data['class_name'],
            class_owner=request_user,
        )
        return class_instance


class StudentInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Student
        fields = ('id', 'first_name', 'last_name', 'patronymic')


class InvitationInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Invitation
        fields = ('invite_code', 'is_used')


class InvitationDetailSerializer(serializers.ModelSerializer):
    invitation = InvitationInfoSerializer(source='*')
    student = StudentInfoSerializer()
    class_info = FullClassNameSerializer(source='student.student_class')

    class Meta:
        model = models.Invitation
        fields = ('invitation', 'student', 'class_info')

