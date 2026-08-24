import EventKit
import Foundation
import ObjectiveC.runtime

struct EventSpec: Decodable {
    let calendar: String
    let date: String
    let start_time: String
    let duration_minutes: Int
    let title: String
    let reminder_at: [String]
    let notes: String
}

struct DeleteSpec: Decodable {
    let calendar: String
    let date: String
    let title: String
}

struct Plan: Decodable {
    let calendar_events: [EventSpec]
    let calendar_delete_scope: [DeleteSpec]
}

enum BridgeError: Error, CustomStringConvertible {
    case message(String)

    var description: String {
        switch self {
        case .message(let value):
            return value
        }
    }
}

func emit(_ value: Any, to handle: FileHandle = .standardOutput) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    handle.write(data)
    handle.write(Data("\n".utf8))
}

func authorizationName(_ status: EKAuthorizationStatus) -> String {
    switch status {
    case .notDetermined: return "notDetermined"
    case .restricted: return "restricted"
    case .denied: return "denied"
    case .authorized: return "authorized"
    case .fullAccess: return "fullAccess"
    case .writeOnly: return "writeOnly"
    @unknown default: return "unknown"
    }
}

func requestFullAccess(_ store: EKEventStore) -> (Bool, String?) {
    var finished = false
    var granted = false
    var errorText: String?
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { value, error in
            granted = value
            errorText = error?.localizedDescription
            finished = true
        }
    } else {
        store.requestAccess(to: .event) { value, error in
            granted = value
            errorText = error?.localizedDescription
            finished = true
        }
    }
    while !finished {
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    }
    return (granted, errorText)
}

func localDate(_ value: String) throws -> Date {
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone.current
    formatter.dateFormat = "yyyy-MM-dd HH:mm"
    guard let result = formatter.date(from: value) else {
        throw BridgeError.message("Invalid local Calendar datetime: \(value)")
    }
    return result
}

func localDateText(_ value: Date) -> String {
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone.current
    formatter.dateFormat = "yyyy-MM-dd HH:mm"
    return formatter.string(from: value)
}

func normalizedNotes(_ value: String?) -> String {
    return (value ?? "")
        .components(separatedBy: .whitespacesAndNewlines)
        .filter { !$0.isEmpty }
        .joined(separator: " ")
}

private let defaultAlarmWasDeletedGetter = NSSelectorFromString("defaultAlarmWasDeleted")
private let defaultAlarmWasDeletedSetter = NSSelectorFromString("setDefaultAlarmWasDeleted:")
private typealias BoolGetter = @convention(c) (AnyObject, Selector) -> Bool
private typealias BoolSetter = @convention(c) (AnyObject, Selector, Bool) -> Void

func defaultAlarmIsSuppressed(_ event: EKEvent) throws -> Bool {
    guard event.responds(to: defaultAlarmWasDeletedGetter) else {
        throw BridgeError.message(
            "EventKit does not expose the per-event default-alarm suppression state"
        )
    }
    let implementation = event.method(for: defaultAlarmWasDeletedGetter)
    let getter = unsafeBitCast(implementation, to: BoolGetter.self)
    return getter(event, defaultAlarmWasDeletedGetter)
}

func suppressDefaultAlarm(_ event: EKEvent) throws {
    guard event.responds(to: defaultAlarmWasDeletedSetter) else {
        throw BridgeError.message(
            "EventKit does not expose the per-event default-alarm suppression setter"
        )
    }
    let implementation = event.method(for: defaultAlarmWasDeletedSetter)
    let setter = unsafeBitCast(implementation, to: BoolSetter.self)
    setter(event, defaultAlarmWasDeletedSetter, true)
}

func calendars(named title: String, in store: EKEventStore) throws -> [EKCalendar] {
    let matches = store.calendars(for: .event).filter { $0.title == title }
    guard matches.count == 1 else {
        throw BridgeError.message("Expected exactly one EventKit calendar named \(title); found \(matches.count)")
    }
    return matches
}

func dayBounds(_ value: String) throws -> (Date, Date) {
    let start = try localDate("\(value) 00:00")
    guard let end = Calendar.current.date(byAdding: .day, value: 1, to: start) else {
        throw BridgeError.message("Unable to build Calendar day boundary: \(value)")
    }
    return (start, end)
}

func matchingEvents(
    calendarName: String,
    date: String,
    title: String,
    store: EKEventStore
) throws -> [EKEvent] {
    let selectedCalendars = try calendars(named: calendarName, in: store)
    let (start, end) = try dayBounds(date)
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: selectedCalendars)
    return store.events(matching: predicate).filter { $0.title == title }
}

func exactEvent(_ spec: EventSpec, in store: EKEventStore) throws -> EKEvent {
    let expectedStart = try localDate("\(spec.date) \(spec.start_time)")
    let matches = try matchingEvents(
        calendarName: spec.calendar,
        date: spec.date,
        title: spec.title,
        store: store
    ).filter {
        abs($0.startDate.timeIntervalSince(expectedStart)) < 1.0 &&
        Int(($0.endDate.timeIntervalSince($0.startDate) / 60.0).rounded()) == spec.duration_minutes
    }
    guard matches.count == 1 else {
        throw BridgeError.message(
            "Expected exactly one EventKit event: \(spec.date) \(spec.title); found \(matches.count)"
        )
    }
    return matches[0]
}

func eventResult(_ event: EKEvent, spec: EventSpec) throws -> [String: Any] {
    let reminderDates = (event.alarms ?? []).compactMap { alarm -> Date? in
        if let absolute = alarm.absoluteDate { return absolute }
        return event.startDate.addingTimeInterval(alarm.relativeOffset)
    }.sorted()
    return [
        "calendar": event.calendar.title,
        "date": localDateText(event.startDate).prefix(10).description,
        "start_time": String(localDateText(event.startDate).suffix(5)),
        "duration_minutes": Int((event.endDate.timeIntervalSince(event.startDate) / 60.0).rounded()),
        "title": event.title ?? "",
        "reminder_at": reminderDates.map(localDateText),
        "notes": normalizedNotes(event.notes),
        "default_alarm_suppressed": try defaultAlarmIsSuppressed(event),
    ]
}

func inspect(_ plan: Plan, store: EKEventStore) throws -> [[String: Any]] {
    return try plan.calendar_events.map { spec in
        try eventResult(try exactEvent(spec, in: store), spec: spec)
    }
}

func repair(_ plan: Plan, store: EKEventStore) throws {
    let targets = try plan.calendar_events.map { spec in
        (try exactEvent(spec, in: store), spec)
    }
    for (event, spec) in targets {
        event.notes = spec.notes
        event.alarms = try spec.reminder_at.map { EKAlarm(absoluteDate: try localDate($0)) }
        try suppressDefaultAlarm(event)
        try store.save(event, span: .thisEvent, commit: false)
    }
    try store.commit()
}

func replace(_ plan: Plan, store: EKEventStore) throws {
    for scope in plan.calendar_delete_scope {
        let matches = try matchingEvents(
            calendarName: scope.calendar,
            date: scope.date,
            title: scope.title,
            store: store
        )
        for event in matches {
            try store.remove(event, span: .thisEvent, commit: false)
        }
    }
    for spec in plan.calendar_events {
        let event = EKEvent(eventStore: store)
        event.calendar = try calendars(named: spec.calendar, in: store)[0]
        event.title = spec.title
        event.startDate = try localDate("\(spec.date) \(spec.start_time)")
        event.endDate = event.startDate.addingTimeInterval(TimeInterval(spec.duration_minutes * 60))
        event.notes = spec.notes
        event.alarms = try spec.reminder_at.map { EKAlarm(absoluteDate: try localDate($0)) }
        try suppressDefaultAlarm(event)
        try store.save(event, span: .thisEvent, commit: false)
    }
    try store.commit()
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw BridgeError.message("Usage: calendar_eventkit.swift <status|request-access|inspect|repair|replace>")
    }
    let mode = CommandLine.arguments[1]
    let store = EKEventStore()
    if mode == "status" {
        let status = EKEventStore.authorizationStatus(for: .event)
        emit(["status": "ok", "authorization": authorizationName(status)])
        exit(0)
    }
    if mode == "request-access" {
        let (granted, errorText) = requestFullAccess(store)
        let status = EKEventStore.authorizationStatus(for: .event)
        emit([
            "status": granted ? "granted" : "denied",
            "authorization": authorizationName(status),
            "error": errorText ?? "",
        ])
        exit(granted ? 0 : 2)
    }
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) {
        guard status == .fullAccess else {
            throw BridgeError.message(
                "EventKit full Calendar access is required; current authorization is \(authorizationName(status))"
            )
        }
    } else {
        guard status == .authorized else {
            throw BridgeError.message(
                "EventKit Calendar access is required; current authorization is \(authorizationName(status))"
            )
        }
    }
    let input = FileHandle.standardInput.readDataToEndOfFile()
    let plan = try JSONDecoder().decode(Plan.self, from: input)
    switch mode {
    case "inspect":
        let events = try inspect(plan, store: store)
        emit(["status": "Calendar read back", "event_count": events.count, "events": events])
    case "repair":
        try repair(plan, store: store)
        let verifyStore = EKEventStore()
        let events = try inspect(plan, store: verifyStore)
        emit(["status": "Calendar notes and alarms updated", "event_count": events.count, "events": events])
    case "replace":
        try replace(plan, store: store)
        let verifyStore = EKEventStore()
        let events = try inspect(plan, store: verifyStore)
        emit(["status": "Calendar written", "event_count": events.count, "events": events])
    default:
        throw BridgeError.message("Unknown EventKit mode: \(mode)")
    }
} catch {
    emit(["status": "error", "error": String(describing: error)], to: .standardError)
    exit(1)
}
