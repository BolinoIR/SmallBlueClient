"""Watch a BBB source-schema table without writing GraphQL manually."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def main() -> None:
    bot = sbc.client(SESSION)

    # BBB 3.0.32's notification table; field names are from the bundled schema.
    bot.watch_table(
        sbc.BBBTable.NOTIFICATION,
        "messageId notificationType messageDescription role createdAt",
    )

    @bot.on("table_notification_changed")
    def notifications(rows: list[dict[str, object]]) -> None:
        for row in rows:
            print(f"{row['notificationType']}: {row['messageDescription']}")

    print("Notification watcher is running. Press Ctrl+C to stop.")
    try:
        bot.run()
    finally:
        bot.close()


if __name__ == "__main__":
    main()
