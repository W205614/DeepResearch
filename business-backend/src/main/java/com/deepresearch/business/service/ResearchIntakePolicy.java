package com.deepresearch.business.service;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import com.deepresearch.business.api.Requests;
import com.deepresearch.business.security.WorkspaceIdentity;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

public final class ResearchIntakePolicy {
    private ResearchIntakePolicy(){}
    public static boolean needsClarification(Requests.Run request){
        String topic=request.normalizedTopic().toLowerCase();
        return !"quick".equals(request.getMode()) && List.of("全面研究","深入研究","全面分析","行业趋势","市场前景","帮我研究","comprehensive research")
            .stream().anyMatch(topic::contains);
    }
    public static boolean sameRequest(Requests.Run first,Requests.Run second){
        return first.normalizedTopic().equals(second.normalizedTopic()) && Objects.equals(first.getMode(),second.getMode())
            && Objects.equals(first.getThreadId(),second.getThreadId()) && Objects.equals(first.getDataPolicy(),second.getDataPolicy())
            && Objects.equals(first.getClientRequestId(),second.getClientRequestId()) && first.getAttachmentIds().equals(second.getAttachmentIds());
    }
    public static Requests.Run confirmedRequest(ObjectMapper mapper,Map<String,Object> row){
        Requests.Run request=mapper.readValue(String.valueOf(row.get("request_json")),Requests.Run.class);
        Map<?,?> answers=mapper.readValue(String.valueOf(row.get("answers_json")),Map.class);
        if(!answers.containsKey("basis"))request.setTopic(request.normalizedTopic()+"\n用户确认口径：时间="+answers.get("time")+"；地区="+answers.get("region")+"；研究对象="+answers.get("object")+"；交付标准="+answers.get("deliverable")+"。未限定的范围属于已披露假设，不代表已验证事实。");
        request.setClarificationId(String.valueOf(row.get("id")));return request;
    }
    public static void requireConfirmed(JdbcTemplate jdbc,ObjectMapper mapper,WorkspaceIdentity identity,Requests.Run request){
        if(!needsClarification(request) && (request.getClarificationId()==null || request.getClarificationId().isBlank()))return;
        List<Map<String,Object>> rows=jdbc.queryForList("SELECT * FROM research_intakes WHERE id=? AND workspace_id=? AND subject=? FOR UPDATE",
            request.getClarificationId(),identity.workspaceId(),identity.subject());
        if(rows.isEmpty())throw new ResponseStatusException(HttpStatus.CONFLICT,"请先确认研究范围、时间、地区和交付标准");
        var row=rows.getFirst();
        if(!"confirmed".equals(row.get("status")) || ((Number)row.get("expires_at")).longValue()<=Instant.now().getEpochSecond())
            throw new ResponseStatusException(HttpStatus.CONFLICT,"研究范围尚未确认或已过期");
        Requests.Run stored=confirmedRequest(mapper,row);
        if(!sameRequest(stored,request))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"确认内容与提交不一致，请重新确认");
    }
    public static void authorizeRevision(JdbcTemplate jdbc,ObjectMapper mapper,WorkspaceIdentity identity,Requests.Run request){
        // Called only after the existing publication/author/version checks, for an unchanged research scope.
        if(!needsClarification(request))return;
        String id=com.deepresearch.business.security.WorkspaceAccess.uid();request.setClarificationId(id);
        jdbc.update("INSERT INTO research_intakes(id,workspace_id,subject,request_json,status,answers_json,expires_at,created_at,updated_at) VALUES(?,?,?,?,'confirmed',?, ?,?,?)",
            id,identity.workspaceId(),identity.subject(),mapper.writeValueAsString(request),"{\"basis\":\"explicit revision of frozen report scope\"}",
            Instant.now().getEpochSecond()+3600,com.deepresearch.business.security.WorkspaceAccess.now(),com.deepresearch.business.security.WorkspaceAccess.now());
    }
}
