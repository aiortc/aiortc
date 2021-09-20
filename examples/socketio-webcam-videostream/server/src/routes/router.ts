import { Router } from "express";

const router = Router();

router.get("/", (req, resp) => {
    resp.send(`Python client A to Python client B example using socket.io`);
});

export default router;