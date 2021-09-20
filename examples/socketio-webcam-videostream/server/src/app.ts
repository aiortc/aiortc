import express from "express";
import cors from "cors";
import { Server as SocketIOServer} from "socket.io";

import router from "./routes/router";

const app = express();
const PORT = process.env.PORT || 4000;

app.use(cors());
app.use(router);

const server = app.listen(PORT, () => {
    console.log("Server running on port:", PORT);
});

const io = new SocketIOServer(server);

io.on("connection", (socket) => {
    console.log("Got socket.io connection");
    socket.on("offer", (offer) => {
        socket.broadcast.emit("offer", offer);
    });
    socket.on("answer", (answer) => {
        socket.broadcast.emit("answer", answer);
    });
    socket.on("join", () => {
        const room = "testRoomName";
        const roomSockets = io.sockets.adapter.rooms.get(room);
        if (!roomSockets) {
            socket.join(room);
            console.log("Created room:", room);
            socket.emit("initiator");
            return;
        }
        const numClientsInRoom = roomSockets.size;
        if (numClientsInRoom === 0) {
            socket.join(room);
            socket.emit("initiator");
            return;
        }
        io.sockets.in(room).emit('join', room);
        console.log("Room:", room, " now has ", numClientsInRoom + 1, " client(s)");
        socket.join(room);
        socket.broadcast.emit('joined');
    });
});
