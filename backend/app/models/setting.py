from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AppSetting(Base, TimestampMixin):
    """用户在界面上能改的设置。

    为什么不直接改 .env：改 .env 要重启进程才生效，而这些开关（自动下载、
    是否同步过往课程）是用户会随手切换的。放数据库里改完立刻生效，
    .env 里的同名配置退化成"首次启动的默认值"。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
