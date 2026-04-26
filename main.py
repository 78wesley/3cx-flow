from threecx import ThreeCXClient, ODataQuery
from dotenv import load_dotenv
import os

load_dotenv()

client = ThreeCXClient(
    base_url=os.getenv("THREECX_BASE_URL"),
    client_id=os.getenv("THREECX_CLIENT_ID"),
    client_secret=os.getenv("THREECX_CLIENT_SECRET"),
)
routing_list = []
# get all call handling, this includes,
# trunks = client.trunks.list_trunks(
#     ODataQuery()
#     .expand("RoutingRules,ReceiveExtensions,Groups,EmergencyGeoLocations")
#     .filter("Gateway/Type eq 'Provider'")
# )
# queues = client.queues.list(
#     ODataQuery()
#     .select("Name,Number,HolidaysRoute,OutOfOfficeRoute,BreakRoute,ForwardNoAnswer")
#     .expand("Groups")
# )
# ringroups = client.ring_groups.list(
#     ODataQuery()
#     .select("Name,Number,HolidaysRoute,OutOfOfficeRoute,BreakRoute,ForwardNoAnswer")
#     .expand("Groups")
# )
# # receptionists = client.receptionists.list(
# #     ODataQuery()
# #     .select(
# #         "Name,Number,HolidaysRoute,OutOfOfficeRoute,BreakRoute,Forwards,Timeout,TimeoutForwardDN,TimeoutForwardType,TimeoutForwardPeerType,InvalidKeyForwardDN"
# #     )
# #     .expand("Groups,Forwards")
# # )
# # groups = client.groups.list(
# #     ODataQuery()
# #     .select(
# #         "Name,Number,Hours,HolidaysRoute,OutOfOfficeRoute,BreakRoute,OfficeRoute,Id"
# #     )
# #     .filter("not startsWith(Name, '___FAVORITES___')")
# # )
users = client.users.list(
    ODataQuery()
    .select(
        "Groups,DisplayName,Number,ForwardingProfiles,ForwardingExceptions,PrimaryGroupId"
    )
    .expand("Groups,ForwardingProfiles,ForwardingExceptions")
    .filter("not startsWith(Number,'HD')")
    .top(30)
)
# scripts = client.scripts.list(
#     ODataQuery().select("Name,Number,IsRegistered").expand("Groups")
# )

# routing_list.extend(trunks)
# routing_list.extend(queues)
# routing_list.extend(ringroups)
# routing_list.extend(receptionists)
# routing_list.extend(groups)
routing_list.extend(users)
# routing_list.extend(scripts)


print(routing_list)
