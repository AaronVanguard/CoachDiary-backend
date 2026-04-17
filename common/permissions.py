from rest_framework import permissions


class _RolePermission(permissions.BasePermission):
    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return getattr(user, 'role', None) in self.allowed_roles


class IsTeacher(_RolePermission):
    """Доступ только учителям."""
    allowed_roles = ('teacher',)


class IsStudent(_RolePermission):
    """Доступ только ученикам."""
    allowed_roles = ('student',)


class IsTeacherOrStudent(_RolePermission):
    """Доступ учителям и ученикам."""
    allowed_roles = ('teacher', 'student')
