from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from .exceptions import GraphQLError

@dataclass(frozen=True, slots=True)
class Operation:
    query: str
    variables: dict[str, Any]

# Operations are copied from BBB 3.0.32's bbb-graphql-actions and HTML5 client.
CHAT_SEND = "mutation ChatSendMessage($chatId:String!,$chatMessageInMarkdownFormat:String!,$replyToMessageId:String){chatSendMessage(chatId:$chatId,chatMessageInMarkdownFormat:$chatMessageInMarkdownFormat,replyToMessageId:$replyToMessageId)}"
USER_SET_MUTED = "mutation UserSetMuted($userId:String,$muted:Boolean!){userSetMuted(userId:$userId,muted:$muted)}"
MEETING_SET_MUTED = "mutation MeetingSetMuted($muted:Boolean!,$exceptPresenter:Boolean){meetingSetMuted(muted:$muted,exceptPresenter:$exceptPresenter)}"
USER_EJECT = "mutation UserEjectFromMeeting($userId:String!,$banUser:Boolean!){userEjectFromMeeting(userId:$userId,banUser:$banUser)}"
MEETING_END = "mutation MeetingEnd{meetingEnd}"
PRESENTATION_SET_PAGE = "mutation PresentationSetPage($presentationId:String!,$pageId:String!){presentationSetPage(presentationId:$presentationId,pageId:$pageId)}"
# BBB 3.0.32 exposes the presenter bit as ``presenter`` (not ``isPresenter``).
# This is kept in sync with bigbluebutton-html5's VideoStreams subscription.
USERS = """subscription SBCUsers{
user(order_by:[{presenter:desc},{role:asc},{isDialIn:desc},{whiteboardWriteAccess:desc},{nameSortable:asc},{registeredAt:asc},{userId:asc}]){
isDialIn userId meetingId extId name isModerator role color avatar away raiseHand reactionEmoji presenter pinned locked authed mobile bot guest clientType disconnected loggedOut currentlyInMeeting whiteboardWriteAccess
voice{joined muted talking deafened listenOnly voiceUserId listenOnlyInputDevice}
cameras{streamId}
}
}"""
# Kept compatible with BBB 3.0.32's
# ``imports/ui/core/graphql/queries/voiceActivity.ts``. Unlike ``user``, this
# stream reports actual detected speech rather than merely joining audio.
# BBB's dedicated ``user_voice_activity_stream`` is middleware-dependent and
# some stock 3.0 deployments close that subscription. The HTML5 GraphQL test
# client also exposes the authoritative live state as ``user.voice.talking``;
# this standard table subscription works on both variants.
VOICE_ACTIVITY = "subscription SBCVoiceActivity{user{userId name voice{joined muted talking}}}"
MEETING = "subscription SBCMeeting{meeting{meetingId name ended}}"
PRESENTATION = "subscription SBCPresentation{pres_page_curr{presentationId pageId num}}"
CHAT_MESSAGES = "subscription SBCChatMessages($limit:Int!,$offset:Int!){chat_message_public(limit:$limit,offset:$offset,order_by:{createdAt:asc}){messageId chatId message senderName senderRole createdAt user{userId name}}}"
PRIVATE_CHAT_MESSAGES = "subscription SBCPrivateChatMessages($limit:Int!,$offset:Int!){chat_message_private(limit:$limit,offset:$offset,order_by:{createdAt:asc}){messageId chatId message senderId senderName senderRole createdAt recipientHasSeen user{userId name}}}"
CURRENT_USER = "subscription SBCCurrentUser{user_current{userId authToken joined currentlyInMeeting loggedOut ejected joinErrorCode joinErrorMessage meeting{ended}}}"
# The following subscriptions are direct selections from the BBB 3.0.32 HTML5
# client query files. They are intentionally separate from the lightweight
# public API queries above so a disconnect in one optional stream never stops
# the rest of a bot's handlers.
CHATS = "subscription SBCChats{chat(order_by:[{chatId:asc}]){chatId participant{userId name nameSortable role color loggedOut avatar currentlyInMeeting isModerator} totalMessages totalUnread public lastSeenAt}}"
POLL_RESULTS = "subscription SBCPollResults{poll(where:{published:{_eq:true}},order_by:[{publishedAt:desc}],limit:1){ended published publishedAt pollId type questionText responses{optionDesc optionId optionResponsesCount pollResponsesCount}}}"
POLLS = "subscription SBCPolls{poll(order_by:{createdAt:desc}){createdAt ended multipleResponses ownerId pollId published publishedAt publishedShowingAnswer questionText quiz secret type options{optionDesc optionId pollId} responses{optionId optionDesc correctOption optionResponsesCount pollId pollResponsesCount questionText type}}}"
BREAKOUT_ROOMS = "subscription SBCBreakoutRooms{breakoutRoom(order_by:{sequence:asc}){assignedAt breakoutRoomMeetingId durationInSeconds endedAt freeJoin hasJoined inviteDismissedAt isDefaultName isLastAssignedRoom isUserCurrentlyInRoom joinURL name sendInvitationToModerators sequence shortName showInvitation startedAt}}"
CAMERAS = "subscription SBCCameras{user_camera{contentType hasAudio showAsContent streamId userId}}"
GUESTS = "subscription SBCGuests{user_guest{guestLobbyMessage guestStatus isAllowed isDenied isWaiting positionInWaitingQueue userId user{name}}}"
RECORDING = "subscription SBCRecording{meeting_recording{isRecording previousRecordedTimeInSeconds startedAt startedBy stoppedAt stoppedBy}}"
CAPTIONS = "subscription SBCCaptions{caption{captionId captionText captionType createdAt locale userId}}"
WHITEBOARD = "subscription SBCWhiteboard{pres_annotation_curr{annotationId annotationInfo lastUpdatedAt pageId presentationId userId}}"
SCREENSHARE = "subscription SBCScreenshare{screenshare{contentType hasAudio screenshareConf screenshareId startedAt stoppedAt stream vidHeight vidWidth voiceConf}}"
PRESENTATIONS = "subscription SBCPresentations{pres_presentation(order_by:{createdAt:asc}){createdAt current downloadFileExtension downloadFileUri downloadable exportToChatCurrentPage exportToChatHasError exportToChatInProgress exportToChatStatus filenameConverted isDefault name presentationId removable totalPages totalPagesUploaded uploadCompleted uploadErrorDetailsJson uploadErrorMsgKey uploadInProgress uploadTemporaryId}}"
PRESENTATION_UPLOAD_TOKEN = "query SBCPresentationUploadToken($uploadTemporaryId:String!){pres_presentation_uploadToken(where:{uploadTemporaryId:{_eq:$uploadTemporaryId}}){presentationId uploadTemporaryId uploadToken}}"
PLUGIN_DATA = "subscription SBCPluginData{pluginDataChannelEntry{channelName createdAt createdBy entryId payloadJson pluginName subChannelName toRoles}}"
BREAKOUT_LIFECYCLE = "subscription SBCBreakoutLifecycle{breakoutRoom{breakoutRoomMeetingId durationInSeconds endedAt freeJoin name sequence shortName startedAt}}"
TIMER = "subscription SBCTimer{timer{accumulated active songTrack time stopwatch running startedAt elapsed}}"
MEETING_STATE = """subscription SBCMeetingState{meeting{
meetingId name ended durationInSeconds
lockSettings{disableCam disableMic disableNotes disablePrivateChat disablePublicChat hasActiveLockSetting hideUserList hideViewersCursor hideViewersAnnotation webcamsOnlyForModerator lockOnJoin lockOnJoinConfigurable}
screenshare{contentType hasAudio screenshareConf screenshareId startedAt stoppedAt stream vidHeight vidWidth voiceConf}
usersPolicies{guestPolicy guestLobbyMessage webcamsOnlyForModerator multiUserWhiteboardEnabled}
layout{cameraDockAspectRatio cameraDockIsResizing cameraDockPlacement cameraWithFocus currentLayoutType presentationMinimized propagateLayout updatedAt}
breakoutRoomsCommonProperties{durationInSeconds freeJoin sendInvitationToModerators startedAt}
externalVideo{externalVideoId playerCurrentTime playerPlaybackRate playerPlaying externalVideoUrl startedSharingAt stoppedSharingAt updatedAt}
componentsFlags{hasBreakoutRoom hasCameraAsContent hasCaption hasCurrentPresentation hasExternalVideo hasPoll hasScreenshare hasScreenshareAsContent hasSharedNotes hasTimer isSharedNotesPinned isEtherpadSharedNotes showRemainingTime}
}}"""
CURRENT_USER_STATE = """subscription SBCCurrentUserState{user_current{
authToken avatar webcamBackground away clientType color ejectReason ejectReasonCode ejected reactionEmoji extId guest guestStatus whiteboardWriteAccess inactivityWarningDisplay inactivityWarningTimeoutSecs isDialIn isModerator logoutUrl currentlyInMeeting joinErrorCode joinErrorMessage joined locked loggedOut mobile name nameSortable pinned presenter raiseHand registeredAt role speechLocale captionLocale userId
meeting{ended endedReasonCode endedByUserName logoutUrl}
lastBreakoutRoom{isUserCurrentlyInRoom sequence shortName}
breakoutRoomsSummary{totalOfBreakoutRooms totalOfIsUserCurrentlyInRoom totalOfShowInvitation totalOfJoinURL}
cameras{streamId} voice{joined spoke listenOnly deafened listenOnlyInputDevice} userLockSettings{disablePublicChat} sessionCurrent{enforceLayout}
}}"""
USER_JOIN_MEETING = "mutation SBCUserJoinMeeting($authToken:String!,$clientType:String!,$clientIsMobile:Boolean!){userJoinMeeting(authToken:$authToken,clientType:$clientType,clientIsMobile:$clientIsMobile)}"

class GraphQLClient:
    """Small synchronous facade over the GraphQL-over-WebSocket transport."""
    def __init__(self, transport: Any, *, on_error: Callable[[GraphQLError], None] | None = None):
        self.transport = transport
        self.on_error = on_error

    def _raise_errors(self, errors: list[dict[str, Any]] | list[Any]) -> None:
        error = GraphQLError("; ".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors))
        if self.on_error is not None:
            self.on_error(error)
        raise error

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.transport.execute(query, variables or {})
        if response.get("errors"):
            self._raise_errors(response["errors"])
        return response.get("data", response)
    def mutation(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]: return self.execute(query, variables)
    def subscribe(self, query: str, variables: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        for response in self.transport.subscribe(query, variables or {}):
            if response.get("errors"):
                self._raise_errors(response["errors"])
            yield response.get("data", response)
